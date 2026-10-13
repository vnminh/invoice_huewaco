from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import csv
import io
import json
import logging
import os
from pathlib import Path
import tempfile
from threading import Event, RLock
from typing import Literal
import uuid
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import String, cast, delete, func, or_, select, text, update
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from starlette.background import BackgroundTask

from .benchmark import run_benchmark, safe_csv
from .core import Core, posting_in, text_tokens
from .db import (Alias, Base, CollectionMember, Customer, HardNegative, KnowledgeReceipt, NumericFeature,
                 NumericSlot, Pattern, PaymentTemplate, Payer, Posting, default_url, make_engine, sessions)
from .excel import ExcelTransaction, StreamingWorkbook, STANDARD_LAYOUTS, layout_guidance
from .exports import csv_archive, result_workbook, sheet_names
from .knowledge import ImportCancelled, import_confirmed
from .normalize import fold
from .identifiers import confirmed_customer_id, confirmed_customer_ids
from .id_slots import invalidate as invalidate_id_slots
from .name_extraction import NameExtractor
from .payment_templates import payment_route
from .payment_period import with_payment_period
from .platform_utils import portable_filename
from .row_errors import ROW_DATA_ERRORS, RowErrors, prepare_rows
from .workspace import WorkingFiles
from .access import AccessPolicy, SECURITY_HEADERS, actor, expected_revision
from .interactive import (BANK_CHANNELS, alias_key, alias_snapshot, bank_snapshot, record_change,
                          require_revision, revision, scope_digest, sync_alias_postings)
from .db import BankAlias, KnowledgeChange, RecurringLink

ROOT = Path(__file__).resolve().parents[1]
# Development-only fixtures. They are never exposed as buttons/default learning inputs.
TRAIN_SAMPLE = ROOT / 'Data/Ngan hang thang 7-2026 FN.xlsx'
RAW_SAMPLE = ROOT / 'Data/Ngan hang thang 08.2026.xlsx'
TRUTH_SAMPLE = ROOT / 'Data/Ngan hang thang 8-2026 FN.xlsx'


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ClassifyInput(StrictInput):
    transaction: str = Field(min_length=1, max_length=32767)
    payer: str = Field(default='BIDV', min_length=1, max_length=100)
    transaction_date: str = Field(default='', max_length=40)
    amount: float = Field(default=0, ge=0, allow_inf_nan=False)

    @field_validator('transaction')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('Transaction text is empty')
        return value


class FeedbackInput(StrictInput):
    transaction_id: int = Field(gt=0)
    accepted: bool
    # Several IDs separated by commas confirm one transfer for several customers.
    correct_customer_id: str | None = Field(default=None, min_length=1, max_length=2000)
    customer_name: str = Field(default='', max_length=300)
    payment_mode: Literal['', 'unknown', 'proxy', 'self'] = ''
    provider_kind: Literal['', 'unknown', 'bank', 'wallet', 'other'] = ''
    provider_name: str = Field(default='', max_length=300)


class BulkFeedbackInput(StrictInput):
    transaction_ids: list[int] = Field(min_length=1, max_length=100)


class UpdateInput(StrictInput):
    limit: int = Field(default=1000, ge=1, le=5000)


class ImportInput(StrictInput):
    confirmed: bool = False
    batch_size: int = Field(default=1000, ge=1, le=5000)


class CustomerNameInput(StrictInput):
    name: str = Field(min_length=1, max_length=300)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Customer name is empty')
        return value


class CustomerCreateInput(CustomerNameInput):
    id: str = Field(min_length=1, max_length=100)

    @field_validator('id')
    @classmethod
    def clean_id(cls, value):
        return confirmed_customer_id(value)


class PatternEditInput(ClassifyInput):
    customer_id: str = Field(min_length=1, max_length=100)
    payment_mode: Literal['', 'unknown', 'proxy', 'self'] = ''
    provider_kind: Literal['', 'unknown', 'bank', 'wallet', 'other'] = ''
    provider_name: str = Field(default='', max_length=300)


class PaymentRouteInput(StrictInput):
    payment_mode: Literal['unknown', 'proxy', 'self']
    provider_kind: Literal['unknown', 'bank', 'wallet', 'other'] = 'unknown'
    provider_name: str = Field(default='', max_length=300)


class BulkTemplatePaymentInput(PaymentRouteInput):
    q: str = Field(default='', max_length=300)
    filter_payment_mode: Literal['unknown', 'proxy', 'self'] | None = None
    expected_count: int = Field(ge=1)
    expected_links: int = Field(ge=0)
    confirmed: Literal[True]
    scope_digest: str = Field(min_length=64, max_length=64)


class TemplateEditInput(StrictInput):
    display_name: str = Field(default='', max_length=300)
    description: str = Field(default='', max_length=4000)


class CustomerAliasInput(StrictInput):
    customer_id: str = Field(pattern=r'^[0-9]{6}$')
    alias: str = Field(min_length=2, max_length=300)
    confirmed: Literal[True]


class BankAliasInput(StrictInput):
    alias: str = Field(min_length=2, max_length=100)
    channel_key: Literal[*BANK_CHANNELS]
    note: str = Field(default='', max_length=1000)
    confirmed: Literal[True]


def create_app(engine=None, core=None, runtime_dir=None, access_policy=None):
    if engine is None and not default_url().startswith('postgresql'):
        raise ValueError('The application requires PostgreSQL. SQLite is reserved for isolated tests/benchmarks.')
    engine = engine or make_engine(default_url())
    factory = sessions(engine)
    core = core or Core(candidate_limit=int(os.getenv('CANDIDATE_LIMIT', '40')),
                        auto_threshold=float(os.getenv('AUTO_THRESHOLD', '.9')),
                        review_threshold=float(os.getenv('REVIEW_THRESHOLD', '.6')),
                        match_margin=float(os.getenv('MATCH_MARGIN', '.08')),
                        name_extractor=NameExtractor())
    runtime = Path(runtime_dir or os.getenv('RUNTIME_DIR', ROOT / 'runtime')).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    working = WorkingFiles(runtime)
    # One server process owns working files; two background jobs may run at once.
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='invoice-jobs')
    controls, job_lock, review_lock = {}, RLock(), RLock()
    shutting_down = Event()
    api = FastAPI(title='HueWACO · Đối soát & quản lý kiến thức', version='3.1.0', docs_url=None, redoc_url=None)
    api.state.engine, api.state.core, api.state.executor, api.state.working = engine, core, executor, working
    policy = access_policy or AccessPolicy.environment(testing=engine.dialect.name == 'sqlite')

    @api.middleware('http')
    async def access_boundary(request, call_next):
        denied = policy.authorize(request)
        if denied is not None:
            return denied
        if (request.url.path.startswith('/benchmark') or request.url.path == '/knowledge/import/sample') and os.getenv('ENABLE_DEV_ROUTES') != 'true':
            return JSONResponse({'detail': 'Chức năng phát triển đã tắt.'}, status_code=404)
        identity = actor.set(request.state.actor)
        stamp = expected_revision.set(request.headers.get('if-match'))
        try:
            response = await call_next(request)
            if request.url.path.startswith(('/database/', '/knowledge/')) and response.status_code < 400:
                with factory() as session:
                    response.headers['X-Knowledge-Revision'] = revision(session)
            response.headers.update(SECURITY_HEADERS)
            return response
        finally:
            actor.reset(identity)
            expected_revision.reset(stamp)
    api.mount('/assets', StaticFiles(directory=ROOT / 'web/assets'), name='assets')

    @api.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        logging.getLogger(__name__).error('Knowledge database request failed: %s', type(error).__name__)
        return JSONResponse(status_code=503, content={'detail': 'Knowledge service unavailable; contact the administrator'})

    def lock_writes(session):
        if engine.dialect.name == 'postgresql':
            session.execute(text('SELECT pg_advisory_xact_lock(731026)'))

    def job_record(job_id):
        try:
            return working.job(job_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    def process_feedback(body):
        with review_lock:
            row = working.row(body.transaction_id)
            chosen = (body.correct_customer_id or row.get('customer_id')) if body.accepted else None
            chosen = ', '.join(confirmed_customer_ids(chosen)) if chosen else None
            if row['status'] != 'pending':
                if (body.accepted and row['status'] == 'confirmed' and chosen
                        and set(confirmed_customer_ids(chosen)) == set(confirmed_customer_ids(row['confirmed_customer_id']))):
                    working.abort_review(row)
                    return row
                if not body.accepted and row['status'] == 'rejected':
                    working.abort_review(row)
                    return row
                raise ValueError('This transaction has already been reviewed')
            if body.accepted and not chosen:
                raise ValueError('Choose a customer before accepting')
            payload = {'accepted': body.accepted, 'correct_customer_id': chosen,
                       'customer_name': body.customer_name.strip() if body.accepted else '',
                       'payment_mode': body.payment_mode, 'provider_kind': body.provider_kind,
                       'provider_name': body.provider_name}
            working.begin_review(row, payload)
            try:
                if body.accepted:
                    core.prepare_names([row['raw']])
                with factory.begin() as session:
                    lock_writes(session)
                    result = core.review(session, row, **payload)
                    record_change(session, 'feedback.confirm' if body.accepted else 'feedback.reject',
                                  'customer:' + (chosen or str(row.get('customer_id') or 'unknown'))[:140],
                                  {}, {'customer_ids': confirmed_customer_ids(chosen) if chosen else [],
                                       'payment_mode': body.payment_mode, 'learned': result.get('learned', False)})
            except ValueError:
                working.abort_review(row)
                raise
            # Durable intent remains if PostgreSQL commits but this file update fails.
            # Retrying/restarting repeats the same receipt, never the learning itself.
            working.finish_review(result)
            return result

    def worker(job_id, path, kind, batch_size=1000, cutoff='2026-07-22', remove_file=False):
        row_errors = RowErrors(lambda issue: working.append_row_error(job_id, issue),
                               file=Path(path).name if path else '')
        def check_cancel():
            control = controls.get(job_id)
            if control and control['event'].is_set():
                raise ImportCancelled()
        def progress(counts):
            working.update_job(job_id, progress=counts.get('processed', counts.get('heldout_rows', counts.get('training_rows', 0))),
                               summary={**working.job(job_id)['summary'], **counts, **row_errors.summary()})
        try:
            with job_lock:
                check_cancel()
                working.update_job(job_id, status='running')
            if kind == 'benchmark':
                summary = run_benchmark(RAW_SAMPLE, TRUTH_SAMPLE, runtime / job_id, cutoff, batch_size,
                                        progress, knowledge_engine=engine, core=core)
                total = summary['counts']['heldout_rows']
            elif kind == 'knowledge_import':
                with review_lock, factory.begin() as session:
                    lock_writes(session)
                    record_change(session, 'import.start', 'import:' + job_id, {}, {'file': Path(path).name})
                summary = import_confirmed(engine, core, path, batch_size, progress, check_cancel=check_cancel,
                                           row_errors=row_errors, write_lock=review_lock)
                total = summary['counts'].get('processed', 0)
                with review_lock, factory.begin() as session:
                    lock_writes(session)
                    record_change(session, 'import.confirmed', 'import:' + summary.get('sha256', job_id), {},
                                  {'file': Path(path).name, 'counts': summary['counts']})
            elif kind == 'review_recovery':
                total = 0
                for intent in working.pending_reviews():
                    check_cancel()
                    process_feedback(FeedbackInput(transaction_id=intent['transaction_id'], **intent['payload']))
                    total += 1
                    progress({'processed': total})
                summary = {'processed': total}
            else:
                total, decisions = 0, {}
                progress({'processed': 0, 'decisions': decisions, 'phase': 'reading'})
                with StreamingWorkbook(path, check_cancel=check_cancel, row_errors=row_errors) as workbook:
                    sheets = workbook.raw_sheets()
                    skipped_sheets = workbook.skipped_sheets('raw')
                    sheet_counts = {sheet: 0 for sheet in sheets}
                    progress({'processed': 0, 'decisions': decisions, 'sheets': sheets,
                              'input_sheets': list(workbook.sheets), 'sheet_counts': sheet_counts,
                              'skipped_sheets': skipped_sheets, 'phase': 'reading'})
                    if not sheets:
                        raise ValueError(layout_guidance('raw'))
                    # Publish small groups per sheet, instead of showing zero throughout
                    # preparation and matching of a 1,000-row batch across multiple sheets.
                    for sheet in sheets:
                        for batch in workbook.batches(sheet=sheet, kind='raw', batch_size=min(batch_size, 32)):
                            check_cancel()
                            progress({'processed': total, 'decisions': decisions, 'sheets': sheets,
                                      'sheet_counts': sheet_counts, 'skipped_sheets': skipped_sheets,
                                      'phase': 'classifying', 'current_sheet': sheet, 'current_row': batch[0].row_index})
                            failed = prepare_rows(core, [row for row in batch
                                if row.debit == 0 and row.amount > 0],
                                row_errors, check_cancel)
                            core.prepare_names([row.raw for row in batch if id(row) not in failed], check_cancel)
                            results = []
                            with factory.begin() as session:
                                if engine.dialect.name == 'postgresql':
                                    session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
                                core.check_model(session)
                                for row in batch:
                                    check_cancel()
                                    if row.customer_id:
                                        raise ValueError('Batch classification requires raw bank layout; upload the unfiltered file')
                                    if id(row) in failed:
                                        continue
                                    try:
                                        with session.begin_nested():
                                            if row.debit > 0 or row.amount <= 0:
                                                result = {'decision': 'reject', 'score': 0, 'customer_id': None, 'customer_name': None,
                                                    'evidence': {'reason': 'Non-credit bank transaction', 'reason_vi': 'Giao dịch ghi nợ hoặc không có tiền ghi có.'}}
                                            else:
                                                result = core.classify(session, row.raw, row.payer, payment_mode=row.payment_mode,
                                                                      provider_kind=row.provider_kind, provider_name=row.provider_name)
                                                if row.validation_errors:
                                                    result['decision'] = 'manual_check'
                                                    result['score'] = min(result['score'], .59)
                                                    result['evidence']['transaction_validation_errors'] = row.validation_errors
                                                    result['evidence']['reason_vi'] += ' ' + ' '.join(row.validation_errors)
                                    except (*ROW_DATA_ERRORS, DataError, IntegrityError) as error:
                                        row_errors.record(row.sheet, row.row_index, error, stage='classify')
                                        continue
                                    results.append({**result, 'raw': row.raw, 'payer': row.payer, 'source': Path(path).name,
                                        'sheet': row.sheet, 'reference': row.reference, 'debit': row.debit,
                                        'validation_errors': row.validation_errors, 'row_index': row.row_index,
                                        'date': row.date, 'amount': row.amount,
                                        **(core.extract_names(row.raw) if 'name_extraction' not in result else {})})
                            check_cancel()
                            working.append(job_id, results)
                            for result in results:
                                decisions[result['decision']] = decisions.get(result['decision'], 0) + 1
                                sheet_counts[result['sheet']] += 1
                            total += len(results)
                            progress({'processed': total, 'decisions': decisions, 'sheets': sheets,
                                      'sheet_counts': sheet_counts, 'skipped_sheets': skipped_sheets,
                                      'phase': 'classifying', 'current_sheet': sheet, 'current_row': batch[-1].row_index})
                summary = {'processed': total, 'decisions': decisions, 'sheets': sheets,
                           'input_sheets': list(workbook.sheets),
                           'sheet_counts': sheet_counts, 'skipped_sheets': skipped_sheets}
            with job_lock:
                check_cancel()
                working.update_job(job_id, status='completed', summary={**summary, **row_errors.summary()}, progress=total)
        except ImportCancelled as stopped:
            with job_lock:
                values = {'status': 'cancelled', 'error': None}
                if stopped.summary:
                    values.update(summary={**stopped.summary, **row_errors.summary()}, progress=stopped.summary.get('counts', {}).get('processed', 0))
                working.update_job(job_id, **values)
            if kind == 'knowledge_import':
                try:
                    with review_lock, factory.begin() as session:
                        lock_writes(session)
                        record_change(session, 'import.cancelled', 'import:' + job_id, {},
                                      {'file': Path(path).name, 'counts': stopped.summary.get('counts', {})})
                except SQLAlchemyError:
                    logging.getLogger(__name__).error('Could not record import cancellation; the import start remains in the audit')
        except Exception as error:
            with job_lock:
                message = str(error) if isinstance(error, (ValueError, zipfile.BadZipFile, FileNotFoundError)) else 'Processing failed; contact the administrator'
                working.update_job(job_id, status='failed', error=message)
            logging.getLogger(__name__).exception('Job %s failed', job_id)
        finally:
            with job_lock:
                controls.pop(job_id, None)
            if remove_file and path:
                Path(path).unlink(missing_ok=True)
                if Path(path).parent.parent == runtime / 'uploads':
                    Path(path).parent.rmdir()

    def enqueue(kind, path=None, batch_size=1000, cutoff='2026-07-22', remove_file=False):
        with job_lock:
            if shutting_down.is_set():
                raise HTTPException(503, 'Dịch vụ đang dừng. Vui lòng thử lại sau.')
            job = working.create_job(kind, Path(path).name if path else '')
            job_id = job['id']
            controls[job_id] = {'event': Event(), 'path': path, 'remove_file': remove_file}
            try:
                controls[job_id]['future'] = executor.submit(copy_context().run, worker, job_id, path, kind, batch_size, cutoff, remove_file)
            except Exception:
                controls.pop(job_id, None)
                working.update_job(job_id, status='failed', error='Không thể bắt đầu xử lý tệp.')
                raise
        return {'job_id': job_id, 'status': 'queued'}

    @api.on_event('startup')
    def recover_confirmations():
        if working.pending_reviews():
            enqueue('review_recovery')

    @api.on_event('shutdown')
    def shutdown_jobs():
        cleanup = []
        with job_lock:
            shutting_down.set()
            for job_id, control in list(controls.items()):
                control['event'].set()
                if control['future'].cancel():
                    working.update_job(job_id, status='cancelled')
                    cleanup.append(controls.pop(job_id))
                elif working.job(job_id)['status'] in ('queued', 'running', 'cancelling'):
                    working.update_job(job_id, status='cancelling')
        for control in cleanup:
            if control['remove_file'] and control['path']:
                Path(control['path']).unlink(missing_ok=True)
                Path(control['path']).parent.rmdir()
        executor.shutdown(wait=True, cancel_futures=True)

    def save_upload(file, allow_csv=False):
        filename = Path((file.filename or '').replace('\\', '/')).name
        allowed = ('.xlsx', '.csv') if allow_csv else ('.xlsx',)
        if Path(filename).suffix.lower() not in allowed:
            raise HTTPException(422, 'Upload an XLSX or learning CSV file' if allow_csv else 'Upload an .xlsx workbook')
        folder = runtime / 'uploads' / str(uuid.uuid4())
        filename_budget = 180
        if os.name == 'nt':
            # Leave room for the folder and filename without requiring Windows long-path setup.
            filename_budget = min(filename_budget, 240 - len(str(folder).encode('utf-16-le')) // 2 - 1)
            if filename_budget < 16:
                raise HTTPException(422, 'Đường dẫn lưu tệp quá dài; hãy đặt project hoặc RUNTIME_DIR ở thư mục ngắn hơn')
        filename = portable_filename(filename, max_bytes=filename_budget)
        folder.mkdir(parents=True)
        path = folder / filename
        try:
            size = 0
            with path.open('wb') as output:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        raise HTTPException(413, 'Upload limit is 100 MB')
                    output.write(chunk)
            if path.suffix.lower() == '.xlsx' and not zipfile.is_zipfile(path):
                raise HTTPException(422, 'Invalid XLSX archive')
            return path
        except Exception:
            path.unlink(missing_ok=True)
            folder.rmdir()
            raise
        finally:
            file.file.close()

    @api.get('/', include_in_schema=False)
    def home():
        return FileResponse(ROOT / 'web/index.html', headers={'Cache-Control': 'no-cache'})

    @api.get('/health')
    def health():
        try:
            with factory() as session:
                session.execute(select(func.count(Customer.id)))
                session.execute(select(NumericFeature.value_digest).limit(1))
                session.execute(select(Posting.token_digest).limit(1))
                session.execute(select(CollectionMember.id).limit(1))
                session.execute(select(BankAlias.id).limit(1))
                session.execute(select(KnowledgeChange.id).limit(1))
                session.execute(select(RecurringLink.id).limit(1))
                core.check_model(session)
            return {'status': 'ok'}
        except ValueError:
            return JSONResponse(status_code=409, content={'status': 'configuration_required', 'detail': 'Knowledge service needs maintenance; contact the administrator'})
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={'status': 'setup_required', 'detail': 'Knowledge service unavailable; contact the administrator'})

    @api.get('/stats')
    def stats(job_id: uuid.UUID | None = None):
        with factory() as session:
            return {**working.counts(str(job_id) if job_id else None),
                    'customers': session.scalar(select(func.count(Customer.id))),
                    'patterns': session.scalar(select(func.count(Pattern.id))),
                    'pending_learning': len(working.pending_reviews())}

    @api.get('/customers')
    def customers(q: str = Query(default='', max_length=300), limit: int = Query(default=20, ge=1, le=100)):
        with factory() as session:
            query = select(Customer)
            if q:
                query = query.where(Customer.normalized_name.contains(fold(q), autoescape=True) | Customer.id.contains(q, autoescape=True))
            counts = select(Pattern.customer_id, func.count().label('count')).group_by(Pattern.customer_id).subquery()
            records = session.execute(query.add_columns(func.coalesce(counts.c.count, 0)).outerjoin(counts, counts.c.customer_id == Customer.id)
                                      .order_by(Customer.canonical_name).limit(limit))
            return [{'id': customer.id, 'name': customer.canonical_name, 'pattern_count': count} for customer, count in records]

    @api.post('/knowledge/customers', status_code=201)
    def add_customer(body: CustomerCreateInput):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            if not body.id.strip():
                raise HTTPException(422, 'Customer ID is empty')
            core.check_model(session, writing=True)
            customer = core.ensure_customer(session, body.id.strip(), body.name)
            record_change(session, 'customer.create', 'customer:' + customer.id, {}, {'id': customer.id, 'name': customer.canonical_name})
            return {'id': customer.id, 'name': customer.canonical_name}

    @api.post('/knowledge/patterns', status_code=201)
    def add_pattern(body: PatternEditInput):
        try:
            core.prepare_names([body.transaction])
            with job_lock, review_lock, factory.begin() as session:
                knowledge_edit_lock(session)
                customer = session.get(Customer, body.customer_id.strip())
                if customer is None:
                    raise HTTPException(404, 'Customer not found')
                row = ExcelTransaction(0, body.transaction, date=body.transaction_date, amount=body.amount,
                    payer=body.payer, source='Mẫu do quản trị viên thêm', label_status='confirmed',
                    payment_mode=body.payment_mode, provider_kind=body.provider_kind, provider_name=body.provider_name)
                learned = core.learn(session, row, customer.id, customer.canonical_name)
                record_change(session, 'pattern.create', 'customer:' + customer.id, {}, {'raw': body.transaction, 'learned': learned})
                return {'learned': learned, 'customer_id': customer.id}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    database_models = {mapper.local_table.name: mapper.class_ for mapper in Base.registry.mappers}

    def database_model(table_name):
        model = database_models.get(table_name)
        if model is None:
            raise HTTPException(404, 'Database table not found')
        return model

    def database_row(model, record):
        return {column.name: getattr(record, column.name) for column in model.__table__.columns}

    def database_record(session, model, row_key):
        column = list(model.__table__.primary_key.columns)[0]
        try:
            key = int(row_key) if column.type.python_type is int else row_key
        except ValueError as error:
            raise HTTPException(422, 'Invalid row identifier') from error
        record = session.get(model, key)
        if record is None:
            raise HTTPException(404, 'Database row not found')
        return record

    def template_usage():
        return select(Pattern.template_id, Pattern.customer_id).union_all(
            select(CollectionMember.template_id, CollectionMember.customer_id)).subquery()

    def template_usage_counts(session, template_ids):
        links = template_usage()
        return {template_id: (count, customers) for template_id, count, customers in session.execute(
            select(links.c.template_id, func.count(), func.count(func.distinct(links.c.customer_id)))
            .where(links.c.template_id.in_(template_ids)).group_by(links.c.template_id))}

    @api.get('/database/tables')
    def database_tables():
        with factory() as session:
            return [{'name': name, 'primary_key': list(model.__table__.primary_key.columns)[0].name,
                     'count': session.scalar(select(func.count()).select_from(model)),
                     'columns': [{'name': column.name, 'type': str(column.type),
                                  'nullable': column.nullable, 'primary_key': column.primary_key,
                                  'has_default': column.default is not None or column.server_default is not None,
                                  'references': [foreign_key.target_fullname for foreign_key in column.foreign_keys]}
                                 for column in model.__table__.columns]}
                    for name, model in sorted(database_models.items())]

    def filtered_database_query(model, q='', customer_id=None, template_id=None, payment_mode=None):
        query = select(model)
        if customer_id is not None:
            if 'customer_id' not in model.__table__.columns:
                raise HTTPException(422, 'This table has no customer identifier')
            query = query.where(model.__table__.columns.customer_id == customer_id)
        if template_id is not None:
            if 'template_id' not in model.__table__.columns:
                raise HTTPException(422, 'Nhóm dữ liệu này không có liên kết mẫu giao dịch.')
            query = query.where(model.__table__.columns.template_id == template_id)
        if payment_mode is not None:
            if 'payment_mode' not in model.__table__.columns:
                raise HTTPException(422, 'Nhóm dữ liệu này không có kiểu thanh toán.')
            query = query.where(model.__table__.columns.payment_mode == payment_mode)
        if q.strip():
            # Search scalar fields; do not turn large JSON/vector data into a full-table text scan.
            searchable = [func.lower(cast(column, String)).contains(
                              fold(q.strip()) if column.name in ('normalized_name', 'normalized_alias', 'normalized_text', 'template_text', 'structure')
                              else q.strip().lower(), autoescape=True)
                          for column in model.__table__.columns if str(column.type) not in ('JSON', 'JSONB', 'vector(384)')]
            query = query.where(or_(*searchable))
        return query

    @api.get('/database/tables/{table_name}/rows')
    def database_rows(table_name: str, q: str = Query(default='', max_length=300),
                      customer_id: str | None = Query(default=None, max_length=100),
                      template_id: int | None = Query(default=None, ge=1),
                      payment_mode: Literal['unknown', 'proxy', 'self'] | None = None,
                      offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
        model = database_model(table_name)
        primary_key = list(model.__table__.primary_key.columns)[0]
        query = filtered_database_query(model, q, customer_id, template_id, payment_mode)
        with factory() as session:
            stamp = revision(session)
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            records = list(session.scalars(query.order_by(primary_key.desc()).offset(offset).limit(limit)))
            items = [database_row(model, record) for record in records]
            if model is Customer:
                counts = dict(session.execute(select(Pattern.customer_id, func.count()).where(
                    Pattern.customer_id.in_([record.id for record in records])).group_by(Pattern.customer_id)).all())
                collection_counts = dict(session.execute(select(CollectionMember.customer_id, func.count()).where(
                    CollectionMember.customer_id.in_([record.id for record in records])).group_by(CollectionMember.customer_id)).all())
                for item in items:
                    item['pattern_count'] = counts.get(item['id'], 0)
                    item['collection_count'] = collection_counts.get(item['id'], 0)
            if model is PaymentTemplate:
                counts = template_usage_counts(session, [record.id for record in records])
                compact = dict(session.execute(select(CollectionMember.template_id, func.count()).where(
                    CollectionMember.template_id.in_([record.id for record in records])).group_by(CollectionMember.template_id)).all())
                for item in items:
                    item['link_count'], item['customer_count'] = counts.get(item['id'], (0, 0))
                    item['collection_customer_count'] = compact.get(item['id'], 0)
            result = {'total': total, 'items': items, 'revision': stamp}
            if model is PaymentTemplate:
                links = template_usage()
                result['link_total'] = session.scalar(select(func.count()).select_from(links).where(
                    links.c.template_id.in_(query.with_only_columns(PaymentTemplate.id))))
                result['scope_digest'] = scope_digest(session, query.with_only_columns(PaymentTemplate.id))
            return result

    @api.get('/database/tables/{table_name}/rows/{row_key:path}')
    def database_row_detail(table_name: str, row_key: str):
        model = database_model(table_name)
        with factory() as session:
            stamp = revision(session)
            item = database_row(model, database_record(session, model, row_key))
            if model is Customer:
                item['pattern_count'] = session.scalar(select(func.count()).select_from(Pattern).where(Pattern.customer_id == item['id']))
                item['collection_count'] = session.scalar(select(func.count()).select_from(CollectionMember).where(
                    CollectionMember.customer_id == item['id']))
            if model is PaymentTemplate:
                item['link_count'], item['customer_count'] = template_usage_counts(session, [item['id']]).get(item['id'], (0, 0))
                item['collection_customer_count'] = session.scalar(select(func.count()).select_from(CollectionMember).where(
                    CollectionMember.template_id == item['id']))
            return {**item, '_revision': stamp}

    @api.patch('/knowledge/templates/{template_id}')
    def edit_template(template_id: int, body: TemplateEditInput):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            template = session.get(PaymentTemplate, template_id)
            if template is None:
                raise HTTPException(404, 'Không tìm thấy mẫu giao dịch.')
            before = {'display_name': template.display_name, 'description': template.description}
            template.display_name, template.description = body.display_name.strip(), body.description.strip()
            record_change(session, 'template.describe', 'template:' + str(template_id), before, body.model_dump())
            return {'id': template.id, 'updated': True}

    @api.patch('/knowledge/templates/{template_id}/payment')
    def set_template_payment(template_id: int, body: PaymentRouteInput):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            template = session.get(PaymentTemplate, template_id)
            if template is None:
                raise HTTPException(404, 'Không tìm thấy mẫu giao dịch.')
            route = payment_route('', **body.model_dump())
            before = {key: getattr(template, key) for key in route}
            for key, value in route.items():
                setattr(template, key, value)
            result = session.execute(update(Pattern).where(Pattern.template_id == template_id).values(**route))
            compact = session.scalar(select(func.count()).select_from(CollectionMember).where(CollectionMember.template_id == template_id))
            record_change(session, 'template.payment', 'template:' + str(template_id), before, {**route, 'updated_links': result.rowcount + compact})
            return {'id': template_id, 'updated_links': result.rowcount + compact, **route}

    @api.post('/knowledge/templates/payment/bulk')
    def set_filtered_template_payment(body: BulkTemplatePaymentInput):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            matches = filtered_database_query(PaymentTemplate, body.q, payment_mode=body.filter_payment_mode)
            template_ids = matches.with_only_columns(PaymentTemplate.id)
            count = session.scalar(select(func.count()).select_from(matches.subquery()))
            usage = template_usage()
            links = session.scalar(select(func.count()).select_from(usage).where(usage.c.template_id.in_(template_ids)))
            if count != body.expected_count or links != body.expected_links or scope_digest(session, template_ids) != body.scope_digest:
                raise HTTPException(409, 'Danh sách mẫu hoặc liên kết đã thay đổi. Làm mới danh sách rồi xác nhận lại.')
            route = payment_route('', body.payment_mode, body.provider_kind, body.provider_name)
            # Update links before changing template fields used by the search/filter.
            session.execute(update(Pattern).where(Pattern.template_id.in_(template_ids)).values(**route),
                            execution_options={'synchronize_session': False})
            session.execute(update(PaymentTemplate).where(PaymentTemplate.id.in_(template_ids)).values(**route),
                            execution_options={'synchronize_session': False})
            record_change(session, 'template.payment.bulk', 'templates:filtered',
                          {'scope_digest': body.scope_digest, 'q': body.q, 'filter': body.filter_payment_mode},
                          {**route, 'templates': count, 'links': links})
            return {'updated_templates': count, 'updated_links': links, **route}

    @api.delete('/knowledge/templates/{template_id}')
    def remove_template(template_id: int):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            template = session.get(PaymentTemplate, template_id)
            if template is None:
                raise HTTPException(404, 'Không tìm thấy mẫu giao dịch.')
            if session.scalar(select(Pattern.id).where(Pattern.template_id == template_id).limit(1)):
                raise HTTPException(409, 'Mẫu giao dịch còn liên kết khách hàng. Xóa hoặc chuyển liên kết trước khi xóa mẫu.')
            if session.scalar(select(CollectionMember.id).where(CollectionMember.template_id == template_id).limit(1)):
                raise HTTPException(409, 'Mẫu còn khách hàng thu hộ. Xóa liên kết khách hàng thu hộ trước khi xóa mẫu.')
            session.delete(template)
            record_change(session, 'template.delete', 'template:' + str(template_id), {'text': template.template_text}, {})
            return {'deleted': True}

    @api.delete('/knowledge/collections/{membership_id}')
    def remove_collection_member(membership_id: int):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            member = session.get(CollectionMember, membership_id)
            if member is None:
                raise HTTPException(404, 'Database row not found')
            session.delete(member)
            record_change(session, 'collection.delete', 'collection:' + str(membership_id),
                          {'customer_id': member.customer_id, 'template_id': member.template_id}, {})
            return {'deleted': True}

    @api.patch('/knowledge/patterns/{pattern_id}/payment')
    def edit_link_payment(pattern_id: int, body: PaymentRouteInput):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            pattern = session.get(Pattern, pattern_id)
            if pattern is None:
                raise HTTPException(404, 'Historical pattern not found')
            before = {key: getattr(pattern, key) for key in ('payment_mode', 'provider_kind', 'provider_name')}
            for key, value in payment_route(pattern.raw_example, **body.model_dump()).items():
                setattr(pattern, key, value)
            record_change(session, 'pattern.payment', 'pattern:' + str(pattern_id), before, body.model_dump())
            return {'id': pattern.id, 'updated': True}

    def knowledge_edit_lock(session):
        lock_writes(session)
        require_revision(session)
        if working.active():
            raise HTTPException(409, 'Wait for the active knowledge job to finish before editing')
        if working.pending_reviews():
            raise HTTPException(409, 'Finish pending confirmations before editing knowledge')

    @api.get('/knowledge/bank-channels')
    def bank_channels():
        return {'channels': BANK_CHANNELS}

    @api.get('/knowledge/revision')
    def knowledge_revision():
        with factory() as session:
            return {'revision': revision(session)}

    @api.delete('/knowledge/negatives/{negative_id}')
    def remove_negative(negative_id: int):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            negative = session.get(HardNegative, negative_id)
            if negative is None:
                raise HTTPException(404, 'Database row not found')
            before = {'customer_id': negative.customer_id, 'transaction_fingerprint': negative.transaction_fingerprint,
                      'pattern_id': negative.pattern_id, 'count': negative.count}
            session.delete(negative)
            record_change(session, 'negative.delete', 'negative:' + str(negative_id), before, {})
            return {'deleted': True}

    @api.post('/knowledge/aliases', status_code=201)
    def add_customer_alias(body: CustomerAliasInput):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            customer = session.get(Customer, body.customer_id)
            if customer is None:
                raise HTTPException(404, 'Customer not found')
            name = ' '.join(body.alias.split())
            normalized = ' '.join(fold(name).split())
            if len(normalized) < 2 or not any(c.isalpha() for c in normalized):
                raise HTTPException(422, 'Bí danh khách hàng phải có tên bằng chữ.')
            existing = session.scalar(select(Alias).where(Alias.customer_id == customer.id, Alias.normalized_alias == normalized))
            before = alias_snapshot(existing) if existing else {}
            core.ensure_alias(session, customer, name, confidence=1.)
            session.flush()
            alias = session.scalar(select(Alias).where(Alias.customer_id == customer.id, Alias.normalized_alias == normalized))
            change = record_change(session, 'alias.confirm' if before else 'alias.create', 'alias:' + str(alias.id),
                                   before, alias_snapshot(alias), reversible=True)
            # Shared names are allowed; they never become a unique identity signal.
            owners = list(session.scalars(select(Alias.customer_id).where(Alias.normalized_alias == normalized)))
            return {'id': alias.id, 'change_id': change.id, 'shared_customer_ids': owners}

    @api.delete('/knowledge/aliases/{alias_id}')
    def remove_customer_alias(alias_id: int):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            alias = session.get(Alias, alias_id)
            if alias is None:
                raise HTTPException(404, 'Database row not found')
            customer = session.get(Customer, alias.customer_id)
            if alias.normalized_alias == ' '.join(fold(customer.canonical_name).split()):
                raise HTTPException(409, 'Đổi tên chính của khách hàng trước khi xóa bí danh tên chính.')
            before = alias_snapshot(alias)
            session.delete(alias)
            session.flush()
            sync_alias_postings(session, customer.id)
            record_change(session, 'alias.delete', 'alias:' + str(alias_id), before, {}, reversible=True)
            return {'deleted': True}

    @api.post('/knowledge/bank-aliases', status_code=201)
    def add_bank_alias(body: BankAliasInput):
        from .identifiers import channel_key
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            key = alias_key(body.alias)
            if session.scalar(select(BankAlias.id).where(BankAlias.normalized_alias == key)):
                raise HTTPException(409, 'Bí danh này đã có. Không ghi đè ngân hàng đã xác nhận.')
            previous = channel_key(key)
            if previous in BANK_CHANNELS and previous != body.channel_key:
                raise HTTPException(409, 'Bí danh trùng kênh ngân hàng đã nhận diện. Không đổi chéo ngân hàng.')
            if previous != body.channel_key and session.scalar(select(Pattern.id).where(Pattern.channel_key == previous).limit(1)):
                raise HTTPException(409, 'Kênh cũ đã có kiến thức. Cần bảo trì để chuyển kênh và dựng lại chỉ mục trước khi thêm bí danh.')
            if previous != body.channel_key and session.scalar(select(PaymentTemplate.id).where(PaymentTemplate.channel_key == previous).limit(1)):
                raise HTTPException(409, 'Kênh cũ đã có mẫu giao dịch. Cần bảo trì trước khi đổi bí danh.')
            alias = BankAlias(alias=body.alias.strip(), normalized_alias=key, channel_key=body.channel_key, note=body.note.strip())
            session.add(alias)
            session.flush()
            record_change(session, 'bank_alias.create', 'bank_alias:' + str(alias.id), {}, bank_snapshot(alias), reversible=True)
            return {'id': alias.id, 'channel_key': alias.channel_key}

    @api.post('/knowledge/changes/{change_id}/undo')
    def undo_knowledge_change(change_id: int):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            change = session.get(KnowledgeChange, change_id)
            if change is None:
                raise HTTPException(404, 'Database row not found')
            if not change.reversible or change.reverted_by:
                raise HTTPException(409, 'Thay đổi này không thể hoàn tác tự động. Sửa lại kiến thức từ bản ghi liên quan.')
            newer = session.scalar(select(KnowledgeChange.id).where(KnowledgeChange.entity_key == change.entity_key,
                KnowledgeChange.id > change.id).limit(1))
            if newer:
                raise HTTPException(409, 'Bản ghi đã có thay đổi mới hơn; kiểm tra bản ghi hiện tại trước khi sửa.')
            before, after = change.before, change.after
            if change.action == 'bank_alias.create':
                alias = session.get(BankAlias, after['id'])
                # Removing an alias after learning through it would split the channel namespace.
                if alias is None or bank_snapshot(alias) != after or alias.used_at:
                    raise HTTPException(409, 'Bí danh đã thay đổi hoặc đã dùng để học; cần bảo trì trước khi gỡ.')
                session.delete(alias)
            elif change.action in ('alias.create', 'alias.confirm', 'alias.delete'):
                snapshot = after or before
                alias = session.get(Alias, snapshot['id'])
                customer = session.get(Customer, snapshot['customer_id'])
                if customer is None or (after and (alias is None or alias_snapshot(alias) != after)) or (not after and alias):
                    raise HTTPException(409, 'Bản ghi đã thay đổi; không thể hoàn tác an toàn.')
                if not before:
                    if alias.normalized_alias == ' '.join(fold(customer.canonical_name).split()):
                        raise HTTPException(409, 'Không xóa bí danh của tên chính hiện tại.')
                    session.delete(alias)
                elif alias:
                    alias.alias, alias.confidence = before['alias'], before['confidence']
                else:
                    if session.scalar(select(Alias.id).where(Alias.customer_id == before['customer_id'], Alias.normalized_alias == before['normalized_alias'])):
                        raise HTTPException(409, 'Bí danh đã được thêm lại; kiểm tra bản ghi hiện tại.')
                    session.add(Alias(**before))
                session.flush()
                sync_alias_postings(session, customer.id)
            else:
                raise HTTPException(409, 'Không hỗ trợ hoàn tác thao tác này.')
            undo = record_change(session, 'change.undo', change.entity_key, after, before)
            change.reverted_by = undo.id
            return {'reverted': True, 'change_id': undo.id}

    @api.patch('/knowledge/customers/{customer_id:path}')
    def rename_customer(customer_id: str, body: CustomerNameInput):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            customer = session.get(Customer, customer_id)
            if not customer:
                raise HTTPException(404, 'Customer not found')
            old_name = customer.normalized_name
            previous_name = customer.canonical_name
            new_name = fold(body.name)
            old_alias = session.scalar(select(Alias).where(Alias.customer_id == customer_id, Alias.normalized_alias == old_name))
            new_alias = session.scalar(select(Alias).where(Alias.customer_id == customer_id, Alias.normalized_alias == new_name))
            if old_alias and old_alias != new_alias:
                session.delete(old_alias)
            if new_alias:
                new_alias.alias = body.name
                new_alias.confidence = 1.0
            else:
                session.add(Alias(customer_id=customer_id, alias=body.name, normalized_alias=new_name, confidence=1.0))
            customer.canonical_name = body.name
            customer.normalized_name = new_name
            session.flush()
            # Keep indexed alias retrieval consistent with the corrected name.
            alias_names = list(session.scalars(select(Alias.normalized_alias).where(Alias.customer_id == customer_id)))
            valid_name_tokens = {'name:' + word for name in alias_names for word in text_tokens(name)}
            obsolete_tokens = {'name:' + word for word in text_tokens(old_name)} - valid_name_tokens
            pattern_ids = list(session.scalars(select(Pattern.id).where(Pattern.customer_id == customer_id)))
            if obsolete_tokens:
                session.execute(delete(Posting).where(Posting.pattern_id.in_(pattern_ids), posting_in(obsolete_tokens)))
            existing = set(session.execute(select(Posting.pattern_id, Posting.token).where(
                Posting.pattern_id.in_(pattern_ids), posting_in(valid_name_tokens))).all())
            session.add_all([Posting(pattern_id=pattern_id, token=token, weight=2.0)
                             for pattern_id in pattern_ids for token in valid_name_tokens
                             if (pattern_id, token) not in existing])
            record_change(session, 'customer.rename', 'customer:' + customer_id, {'name': previous_name}, {'name': body.name})
            return {'id': customer.id, 'name': customer.canonical_name}

    def delete_patterns(session, pattern_ids):
        positions = {(channel, segment['context']) for channel, segments in session.execute(
            select(Pattern.channel_key, Pattern.segments).where(Pattern.id.in_(pattern_ids)))
            for segment in segments if segment.get('context')}
        invalidate_id_slots(session, positions)
        # A rejected customer remains rejected even after its old pattern is removed.
        session.execute(update(HardNegative).where(HardNegative.pattern_id.in_(pattern_ids)).values(pattern_id=None))
        for model in (Posting, NumericFeature, NumericSlot, RecurringLink):
            session.execute(delete(model).where(model.pattern_id.in_(pattern_ids)))
        session.execute(delete(Pattern).where(Pattern.id.in_(pattern_ids)))

    def prune_payers(session, customer_id):
        patterns = list(session.scalars(select(Pattern).where(Pattern.customer_id == customer_id)))
        referenced = {pattern.payer_id for pattern in patterns}
        accounts = set(session.scalars(select(NumericFeature.numeric_value).join(
            Pattern, Pattern.id == NumericFeature.pattern_id).where(
                Pattern.customer_id == customer_id, NumericFeature.numeric_type.in_(['ACCOUNT_ID', 'CARD_ID']))))
        for payer in session.scalars(select(Payer).where(Payer.customer_id == customer_id)):
            if payer.id not in referenced and (payer.payer_type != 'account' or payer.payer_name not in accounts):
                session.delete(payer)

    @api.patch('/knowledge/patterns/{pattern_id}')
    def edit_pattern(pattern_id: int, body: PatternEditInput):
        try:
            core.prepare_names([body.transaction])
            with job_lock, review_lock, factory.begin() as session:
                knowledge_edit_lock(session)
                pattern = session.get(Pattern, pattern_id)
                if pattern is None:
                    raise HTTPException(404, 'Historical pattern not found')
                customer = session.get(Customer, body.customer_id.strip())
                if customer is None:
                    raise HTTPException(404, 'Customer not found')
                if pattern.raw_example == body.transaction and pattern.customer_id == customer.id:
                    return {'updated': False, 'customer_id': customer.id}
                old_customer_id = pattern.customer_id
                payer = session.get(Payer, pattern.payer_id) if pattern.payer_id else None
                source = pattern.source_file
                if not source.endswith(' (đã sửa thủ công)'):
                    source += ' (đã sửa thủ công)'
                transaction = ExcelTransaction(row_index=pattern.source_row, raw=body.transaction,
                    date=pattern.example_date, source=source, payer=payer.payer_name if payer else pattern.channel_key,
                    customer_id=customer.id, customer_name=customer.canonical_name, label_status='confirmed',
                    payment_mode=pattern.payment_mode, provider_kind=pattern.provider_kind, provider_name=pattern.provider_name)
                core.check_model(session)
                delete_patterns(session, [pattern_id])
                core.learn(session, transaction, customer.id, customer.canonical_name,
                           receipt_key='pattern-edit:' + uuid.uuid4().hex)
                session.flush()
                prune_payers(session, old_customer_id)
                if old_customer_id != customer.id:
                    prune_payers(session, customer.id)
                record_change(session, 'pattern.correct', 'pattern:' + str(pattern_id),
                              {'customer_id': old_customer_id, 'raw': pattern.raw_example},
                              {'customer_id': customer.id, 'raw': body.transaction})
                return {'updated': True, 'customer_id': customer.id}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @api.delete('/knowledge/patterns/{pattern_id}')
    def remove_pattern(pattern_id: int):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            pattern = session.get(Pattern, pattern_id)
            if pattern is None:
                raise HTTPException(404, 'Historical pattern not found')
            customer_id = pattern.customer_id
            delete_patterns(session, [pattern_id])
            prune_payers(session, customer_id)
            record_change(session, 'pattern.delete', 'pattern:' + str(pattern_id), {'customer_id': customer_id, 'raw': pattern.raw_example}, {})
            return {'deleted': True, 'pattern_id': pattern_id}

    @api.delete('/knowledge/customers/{customer_id:path}')
    def remove_customer(customer_id: str):
        with job_lock, review_lock, factory.begin() as session:
            knowledge_edit_lock(session)
            if session.get(Customer, customer_id) is None:
                raise HTTPException(404, 'Customer not found')
            pattern_ids = list(session.scalars(select(Pattern.id).where(Pattern.customer_id == customer_id)))
            delete_patterns(session, pattern_ids)
            for model in (Alias, Payer, KnowledgeReceipt, HardNegative, CollectionMember):
                session.execute(delete(model).where(model.customer_id == customer_id))
            session.execute(delete(Customer).where(Customer.id == customer_id))
            record_change(session, 'customer.delete', 'customer:' + customer_id, {'patterns': len(pattern_ids)}, {})
            return {'deleted': True, 'customer_id': customer_id, 'patterns_deleted': len(pattern_ids)}

    @api.post('/classify')
    def classify(body: ClassifyInput):
        try:
            with factory() as session:
                result = with_payment_period({**core.classify(session, body.transaction, body.payer), 'raw': body.transaction})
            job = working.create_job('batch_classify', 'Giao dịch nhập trực tiếp')
            ids = working.append(job['id'], [{**result, 'raw': body.transaction, 'payer': body.payer,
                'source': 'Nhập trực tiếp', 'row_index': None, 'date': body.transaction_date, 'amount': body.amount}])
            working.update_job(job['id'], status='completed', progress=1, summary={'processed': 1})
            return {**result, 'transaction_id': ids[0], 'job_id': job['id']}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @api.post('/feedback')
    def feedback(body: FeedbackInput):
        try:
            result = process_feedback(body)
            return {'transaction_id': result['id'], 'status': result['status'],
                    'confirmed_customer_id': result['confirmed_customer_id'], 'learned': result['learned']}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @api.get('/transactions/{transaction_id}/candidates')
    def transaction_candidates(transaction_id: int, q: str = Query(default='', max_length=100),
                               offset: int = Query(default=0, ge=0, le=1000000),
                               limit: int = Query(default=20, ge=1, le=50)):
        try:
            row = working.row(transaction_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        with factory() as session:
            return core.recommendation_group(session, row['raw'], row.get('payer', 'unknown'),
                names=row.get('extracted_names', []), suggestions=row.get('recommendations'), q=q, offset=offset, limit=limit,
                include_examples=True)

    @api.post('/feedback/batch')
    def bulk_feedback(body: BulkFeedbackInput):
        accepted, errors = [], []
        for row_id in dict.fromkeys(body.transaction_ids):
            try:
                row = working.row(row_id)
                if not row.get('customer_id') or row.get('decision') != 'auto_accept':
                    raise ValueError('Gợi ý độ tin cậy thấp hoặc nhiều khách hàng cần được kiểm tra từng dòng trước khi xác nhận.')
                with factory() as session:
                    if session.get(Customer, row['customer_id']) is None:
                        raise ValueError('Customer not found')
                result = process_feedback(FeedbackInput(transaction_id=row_id, accepted=True))
                accepted.append(result['id'])
            except ValueError as error:
                errors.append({'transaction_id': row_id, 'error': str(error)})
            except SQLAlchemyError:
                errors.append({'transaction_id': row_id, 'error': 'Knowledge service unavailable; contact the administrator'})
                break
        return {'confirmed': accepted, 'errors': errors}

    @api.post('/reviews/retry', status_code=202)
    def retry_reviews():
        with job_lock:
            if any(job['kind'] == 'review_recovery' and job['status'] in ('queued', 'running', 'cancelling') for job in working.list_jobs()):
                raise HTTPException(409, 'Confirmation recovery is already running')
            return enqueue('review_recovery')

    @api.post('/knowledge/update', deprecated=True)
    def knowledge_update(body: UpdateInput):
        # Compatibility only: confirmation now already commits learning immediately.
        return {'updated': 0, 'automatic_on_confirmation': True, 'pending_learning': len(working.pending_reviews())}

    @api.post('/knowledge/import', status_code=202)
    def import_knowledge(file: UploadFile = File(...), confirmed: bool = Form(False),
                         batch_size: int = Form(1000, ge=1, le=5000)):
        if not confirmed:
            raise HTTPException(422, 'Confirm that these customer labels were reviewed before importing knowledge')
        path = save_upload(file, allow_csv=True)
        try:
            return enqueue('knowledge_import', path, batch_size, remove_file=True)
        except Exception:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
            raise

    @api.post('/knowledge/import/sample', status_code=202, include_in_schema=False)
    def import_sample(body: ImportInput):
        if not body.confirmed:
            raise HTTPException(422, 'Confirm these labels before importing')
        if not TRAIN_SAMPLE.exists():
            raise HTTPException(404, 'Bundled FN file not found')
        return enqueue('knowledge_import', TRAIN_SAMPLE, body.batch_size)

    @api.post('/batch/classify', status_code=202)
    def batch_classify(file: UploadFile = File(...), batch_size: int = Form(1000, ge=1, le=5000)):
        path = save_upload(file)
        try:
            return enqueue('batch_classify', path, batch_size, remove_file=True)
        except Exception:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
            raise

    @api.post('/batch/classify/sample', status_code=202, include_in_schema=False)
    def batch_sample(body: UpdateInput):
        if not RAW_SAMPLE.exists():
            raise HTTPException(404, 'Bundled raw file not found')
        return enqueue('batch_classify', RAW_SAMPLE, body.limit)

    @api.get('/jobs/{job_id}')
    def get_job(job_id: uuid.UUID):
        return job_record(str(job_id))

    @api.get('/jobs/{job_id}/errors.csv')
    def export_row_errors(job_id: uuid.UUID):
        job_record(str(job_id))
        def stream():
            yield '\ufeff'
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(['TEP', 'SHEET', 'DONG', 'VI_TRI_XML', 'GIAI_DOAN', 'LOI'])
            yield buffer.getvalue()
            for issue in working.row_errors(str(job_id)):
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow([safe_csv(issue.get('file')), safe_csv(issue.get('sheet')),
                    issue.get('row_index'), issue.get('row_position'), safe_csv(issue.get('stage')),
                    safe_csv(issue.get('error'))])
                yield buffer.getvalue()
        return StreamingResponse(stream(), media_type='text/csv',
            headers={'Content-Disposition': f'attachment; filename="row-errors-{job_id}.csv"'})

    @api.post('/jobs/{job_id}/cancel')
    def cancel_job(job_id: uuid.UUID):
        job_id = str(job_id)
        cleanup = None
        with job_lock:
            job = job_record(job_id)
            if job['status'] not in ('queued', 'running', 'cancelling'):
                return {'job_id': job_id, 'status': job['status']}
            if job['kind'] == 'benchmark':
                raise HTTPException(409, 'Development benchmark cannot be stopped from this screen')
            control = controls.get(job_id)
            if not control:
                raise HTTPException(409, 'Worker is no longer active; upload the file again')
            control['event'].set()
            if control['future'].cancel():
                status = 'cancelled'
                cleanup = controls.pop(job_id)
            else:
                status = 'cancelling'
            working.update_job(job_id, status=status)
        if cleanup and cleanup['remove_file'] and cleanup['path']:
            Path(cleanup['path']).unlink(missing_ok=True)
            Path(cleanup['path']).parent.rmdir()
        return {'job_id': job_id, 'status': status}

    @api.delete('/jobs/{job_id}')
    def delete_job(job_id: uuid.UUID):
        try:
            with job_lock, review_lock:
                job = job_record(str(job_id))
                working.remove_job(str(job_id))
                if job['kind'] == 'benchmark':
                    import shutil
                    shutil.rmtree(runtime / str(job_id), ignore_errors=True)
            return {'deleted': True, 'knowledge_preserved': True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @api.get('/jobs')
    def list_jobs():
        return working.list_jobs()

    @api.get('/patterns/{pattern_id}')
    def pattern_detail(pattern_id: int):
        with factory() as session:
            pattern = session.get(Pattern, pattern_id)
            if not pattern:
                raise HTTPException(404, 'Historical pattern not found')
            customer = session.get(Customer, pattern.customer_id)
            return {'id': pattern.id, 'customer_id': customer.id, 'customer_name': customer.canonical_name,
                    'raw_example': pattern.raw_example, 'template': pattern.template_text,
                    'segments': pattern.segments, 'source_file': pattern.source_file,
                    'source_row': pattern.source_row, 'example_date': pattern.example_date,
                    'seen_count': pattern.seen_count, 'historical_confidence': pattern.confidence}

    @api.get('/transactions')
    def transactions(offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
                     decision: str | None = None, status: str | None = None, job_id: uuid.UUID | None = None,
                     q: str = Query(default='', max_length=300)):
        return working.query(offset, limit, decision, status, str(job_id) if job_id else None, q)

    def temporary_download(filename, media_type, build):
        folder = runtime / 'exports'
        folder.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='download-', dir=folder)
        path = Path(temp.name) / filename
        try:
            build(path)
        except BaseException as error:
            temp.cleanup()
            if isinstance(error, ValueError):
                raise HTTPException(422, str(error)) from error
            raise
        # Handles used for writing are closed before serving/cleaning up on Windows.
        return FileResponse(path, media_type=media_type, filename=filename,
                            background=BackgroundTask(temp.cleanup))

    @api.get('/layouts')
    def standard_layouts():
        return STANDARD_LAYOUTS

    def grouped_export(job_id, kind='csv'):
        job = job_record(str(job_id))
        if job['kind'] != 'batch_classify':
            raise HTTPException(422, 'Only classification files have transaction exports')
        names = sheet_names(job)
        if kind == 'xlsx':
            return temporary_download(f'results-{job_id}.xlsx',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                lambda path: result_workbook(working.rows(str(job_id)), names, path))
        return temporary_download(f'results-{job_id}.zip', 'application/zip',
            lambda path: csv_archive(working.rows(str(job_id)), names, path))

    @api.get('/export/{job_id}')
    def export(job_id: uuid.UUID):
        return grouped_export(job_id)

    @api.get('/export/{job_id}/csv.zip')
    def export_csv_sheets(job_id: uuid.UUID):
        return grouped_export(job_id)

    @api.get('/export/{job_id}/results.xlsx')
    def export_xlsx(job_id: uuid.UUID):
        return grouped_export(job_id, kind='xlsx')

    # The supplied development benchmark stays available to developers, outside the admin workflow.
    @api.post('/benchmark', status_code=202, include_in_schema=False)
    def benchmark(body: ImportInput):
        if not RAW_SAMPLE.exists() or not TRUTH_SAMPLE.exists():
            raise HTTPException(404, 'Development benchmark files not found')
        return enqueue('benchmark', batch_size=body.batch_size)

    @api.get('/benchmark/latest', include_in_schema=False)
    def latest_benchmark():
        path = ROOT / 'reports/august_2026/benchmark.json'
        if not path.exists():
            raise HTTPException(404, 'Development report not found')
        return json.loads(path.read_text(encoding='utf-8'))

    @api.get('/benchmark/{job_id}/download/{artifact}', include_in_schema=False)
    def benchmark_artifact(job_id: uuid.UUID, artifact: str):
        if artifact not in ('benchmark.json', 'benchmark.md', 'predictions.csv', 'evidence.jsonl'):
            raise HTTPException(404, 'Unknown artifact')
        job = job_record(str(job_id))
        path = runtime / str(job_id) / artifact
        if job['kind'] != 'benchmark' or job['status'] != 'completed' or not path.exists():
            raise HTTPException(404, 'Completed benchmark artifact not found')
        return FileResponse(path, filename=artifact)

    return api


app = create_app()
