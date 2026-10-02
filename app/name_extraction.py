"""Local Vietnamese NER; never rewrites text or numeric identifiers."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import logging
import math
import os
from pathlib import Path
import re
from threading import RLock

from .bank_content import mb_water_fields, valid_name
from .normalize import fold

DEFAULT_NER_MODEL = 'NlpHUST/ner-vietnamese-electra-base'
logger = logging.getLogger(__name__)


def name_key(name):
    return ' '.join(re.findall(r'[a-z]+', fold(name)))


def bank_name_entities(raw):
    fields = mb_water_fields(raw)
    if not fields:
        return []
    return [{'name': fields['name'], 'label': 'NAME', 'score': None, 'source': 'bank_field',
             'start': fields['name_span'][0], 'end': fields['name_span'][1]}]


def name_fields(extraction):
    entities = extraction.get('entities', [])
    # A full, explicit bank field takes precedence over a partial model span.
    people = [entity for entity in entities if entity['label'] == 'PERSON']
    bank_fields = [entity for entity in entities if entity['source'].startswith('bank_field')]
    choices = bank_fields or people or entities
    names = list(dict.fromkeys(entity['name'] for entity in choices))
    return {'extracted_name': names[0] if len(names) == 1 else '',
            'extracted_names': names, 'name_extraction': extraction}


def structured_name_fields(raw):
    """Read old result files without loading NER or silently reclassifying them."""
    return name_fields({'status': 'not_run', 'model': None, 'entities': bank_name_entities(raw)})


class NameExtractor:
    def __init__(self):
        enabled = os.getenv('NER_ENABLED', 'true').strip().lower()
        if enabled not in ('true', 'false', '1', '0', 'yes', 'no', 'on', 'off'):
            raise ValueError('NER_ENABLED must be true or false')
        self.enabled = enabled in ('true', '1', 'yes', 'on')
        self.name = os.getenv('NER_MODEL', DEFAULT_NER_MODEL)
        self.revision = os.getenv('NER_REVISION', 'main')
        self.min_score = float(os.getenv('NER_MIN_SCORE', '.85'))
        self.batch_size = int(os.getenv('NER_BATCH_SIZE', '8'))
        if not 0 <= self.min_score <= 1 or not 1 <= self.batch_size <= 32:
            raise ValueError('Invalid NER score threshold or batch size')
        self.device = os.getenv('NER_DEVICE', 'cpu')
        self.folder = str(Path(os.getenv('NER_MODEL_CACHE', 'runtime/models/ner')).resolve())
        self.pipe, self.failure = None, None
        self.lock, self.cache = RLock(), OrderedDict()
        self.cache_limit = 2048

    def _load(self):
        if not self.enabled or self.failure:
            return False
        if self.pipe is None:
            try:
                from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline
                tokenizer = AutoTokenizer.from_pretrained(self.name, revision=self.revision,
                    cache_dir=self.folder, use_fast=True, trust_remote_code=False, model_max_length=512)
                model = AutoModelForTokenClassification.from_pretrained(self.name, revision=self.revision,
                    cache_dir=self.folder, trust_remote_code=False, use_safetensors=True)
                if not tokenizer.is_fast:
                    raise ValueError('NER requires a fast tokenizer for source offsets and long text')
                labels = {str(label).upper().removeprefix('B-').removeprefix('I-')
                          for label in model.config.id2label.values()}
                if not labels.intersection({'PERSON', 'PER'}):
                    raise ValueError('NER model has no PERSON/PER labels')
                model.eval()
                self.pipe = pipeline('token-classification', model=model, tokenizer=tokenizer,
                    aggregation_strategy='simple', stride=64, device=self.device)
            except Exception as error:
                self.failure = type(error).__name__
                logger.warning('NER unavailable during model loading (%s); reconciliation continues', self.failure)
                return False
        return True

    def learning_signature(self):
        # Completed imports may need alias enrichment without relearning patterns.
        return hashlib.sha256(repr(('name-alias-v1', self.enabled, self.name,
                                   self.revision, self.min_score)).encode('utf-8')).hexdigest()

    def _inputs(self, raw):
        # Mask numeric/code tokens only in the model input, keeping every source
        # offset. The original content and deterministic numeric pipeline are untouched.
        chars = list(raw)
        for match in re.finditer(r'\w*\d\w*', raw):
            chars[match.start():match.end()] = [' '] * len(match.group())
        for index, char in enumerate(chars):
            if char in '._/@:;|':
                chars[index] = ' '
        fields = mb_water_fields(raw)
        if fields:
            start, end = fields['receiver_span']
            chars[start:end] = [' '] * (end - start)
        inputs = [(''.join(chars), None)]
        if fields:
            prefix = 'Người chuyển tiền: '
            inputs.append((prefix + fields['name'], {'input_start': len(prefix),
                'raw_start': fields['name_span'][0], 'length': len(fields['name'])}))
        return inputs

    def _entities(self, raw, inputs, predictions):
        entities = bank_name_entities(raw)
        for (content, mapping), results in zip(inputs, predictions):
            for result in results:
                label = str(result.get('entity_group', '')).upper()
                label = {'PER': 'PERSON', 'ORG': 'ORGANIZATION'}.get(label, label)
                score = float(result['score'])
                if label not in ('PERSON', 'ORGANIZATION') or not math.isfinite(score) or score < self.min_score:
                    continue
                start, end = int(result['start']), int(result['end'])
                if not 0 <= start < end <= len(content):
                    continue
                if mapping:
                    if start < mapping['input_start'] or end > mapping['input_start'] + mapping['length']:
                        continue
                    start = mapping['raw_start'] + start - mapping['input_start']
                    end = mapping['raw_start'] + end - mapping['input_start']
                name = raw[start:end].strip()
                if not valid_name(name):
                    continue
                existing = next((entity for entity in entities if name_key(entity['name']) == name_key(name)), None)
                if existing:
                    existing['score'] = max(existing.get('score') or 0, round(score, 4))
                    if existing['source'] == 'bank_field':
                        existing['source'] = 'bank_field+ner'
                        existing['label'] = label
                    continue
                # Keep the complete bank name when the model recognizes only a
                # fragment inside it. Never invent missing accents or name parts.
                if any(entity['start'] <= start and end <= entity['end'] for entity in entities):
                    continue
                entities.append({'name': name, 'label': label, 'score': round(score, 4),
                                 'source': 'ner', 'start': start, 'end': end})
        return entities[:12]

    def _remember(self, raw, value):
        key = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        self.cache[key] = value
        self.cache.move_to_end(key)
        while len(self.cache) > self.cache_limit:
            self.cache.popitem(last=False)

    def prepare(self, raws, check_cancel=None):
        check_cancel = check_cancel or (lambda: None)
        with self.lock:
            pending = list(dict.fromkeys(str(raw) for raw in raws
                if hashlib.sha256(str(raw).encode('utf-8')).hexdigest() not in self.cache))
            if not pending:
                return
            check_cancel()
            available = self._load()
            for start in range(0, len(pending), self.batch_size):
                check_cancel()
                batch = pending[start:start + self.batch_size]
                inputs = [self._inputs(raw) for raw in batch]
                predictions = None
                if available:
                    flat = [content for row in inputs for content, _ in row]
                    try:
                        predictions = list(self.pipe(flat, batch_size=self.batch_size))
                        if len(predictions) != len(flat):
                            raise ValueError('Incomplete NER batch output')
                    except Exception as error:
                        logger.warning('NER batch failed (%s); retrying individual contents', type(error).__name__)
                position = 0
                for raw, row_inputs in zip(batch, inputs):
                    check_cancel()
                    row_predictions = None
                    if predictions is not None:
                        row_predictions = predictions[position:position + len(row_inputs)]
                    elif available:
                        try:
                            row_predictions = [self.pipe(content) for content, _ in row_inputs]
                        except Exception as error:
                            logger.warning('NER row unavailable (%s); original reconciliation continues', type(error).__name__)
                    position += len(row_inputs)
                    try:
                        entities = self._entities(raw, row_inputs, row_predictions) if row_predictions is not None else bank_name_entities(raw)
                        status = 'completed' if row_predictions is not None else 'unavailable' if self.enabled else 'disabled'
                    except (ValueError, TypeError, KeyError, IndexError) as error:
                        logger.warning('Invalid NER entities (%s); using bank fields only', type(error).__name__)
                        entities, status = bank_name_entities(raw), 'unavailable'
                    self._remember(raw, {'status': status, 'model': self.name,
                        'revision': self.revision, 'min_score': self.min_score, 'entities': entities})

    def extract(self, raw):
        raw = str(raw or '')
        key = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        with self.lock:
            if key not in self.cache:
                self.prepare([raw])
            self.cache.move_to_end(key)
            return deepcopy(self.cache[key])
