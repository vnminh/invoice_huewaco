"""Exact identifiers and their order are independent of semantic similarity."""
from difflib import SequenceMatcher

IDENTITY_TYPES = {'CUSTOMER_ID', 'CONTRACT_ID'}
VARIABLE_TYPES = {'BANK_PROTOCOL', 'BANK_REFERENCE', 'REFERENCE_ID', 'INVOICE_ID', 'INVOICE_CODE', 'AMOUNT', 'QUANTITY', 'RECEIVER_ACCOUNT'}
CODE_TYPES = {'MIXED_CODE', 'LOCATION_NUMBER'}


def compare_ordered_numbers(query, stored_segments, stored_template):
    historical = [n for n in stored_segments if n['type'] == 'number']
    lexical = SequenceMatcher(None, query.template, stored_template, autojunk=False).ratio()
    # A bank reference's own letter/digit pattern changes between transfers;
    # only stable identifier formats participate in identity alignment.
    shape = lambda n: (n['numeric_type'], '*' if n['numeric_type'] in VARIABLE_TYPES else n.get('format', '#'))
    structure = SequenceMatcher(None, [shape(n) for n in query.numbers],
                                [shape(n) for n in historical], autojunk=False).ratio()
    template_compatible = lexical >= .70 and structure >= .75
    historical_by_slot = {n['slot']: n for n in historical}
    query_stable = [n for n in query.numbers if n['numeric_type'] not in VARIABLE_TYPES]
    history_stable = [n for n in historical if n['numeric_type'] not in VARIABLE_TYPES]
    # Optional changing references may be inserted/omitted by the bank. Align
    # identity fields only if their COMPLETE role/format sequence still agrees.
    # This preserves identity order and never uses an unordered set of numbers.
    stable_sequence_matches = template_compatible and [shape(n) for n in query_stable] == [shape(n) for n in history_stable]
    aligned_stable = {q['slot']: h for q, h in zip(query_stable, history_stable)} if stable_sequence_matches else {}
    rows = []
    for number in query.numbers:
        old = aligned_stable.get(number['slot'], historical_by_slot.get(number['slot']))
        same_role = bool(old and number['numeric_type'] == old['numeric_type'])
        exact = bool(old and number['value'] == old['value'])
        rows.append({'slot': number['slot'], 'query_value': number['value'],
                     'history_value': old['value'] if old else None, 'type': number['numeric_type'],
                     'history_type': old['numeric_type'] if old else None,
                     'history_slot': old['slot'] if old else None,
                     'absolute_slot_match': bool(old and old['slot'] == number['slot']),
                     'same_role': same_role, 'exact': exact,
                     'same_format': bool(old and number.get('format', '#') == old.get('format', '#')),
                     'variable_reference': number['numeric_type'] in VARIABLE_TYPES})
    # Contracts are stable identity evidence, unlike changing invoice/bank references.
    q_contracts = [n['value'] for n in query.numbers if n['numeric_type'] == 'CONTRACT_ID']
    h_contracts = [n['value'] for n in historical if n['numeric_type'] == 'CONTRACT_ID']
    contract_conflict = bool(q_contracts and h_contracts and q_contracts != h_contracts)
    aligned_contracts = [r['query_value'] for r in rows if r['type'] == 'CONTRACT_ID'
                         and r['same_role'] and r['exact'] and r['same_format']]
    # A bare token is usable only if a confirmed historical label bound that exact token to
    # this customer, and the current transaction has the same numeric position/role/format.
    bound_ids = [r['query_value'] for r in rows if r['same_role'] and r['exact'] and r['same_format']
                 and r['absolute_slot_match'] and historical_by_slot[r['slot']].get('confirmed_customer_value') and template_compatible]
    unresolved_bound_ids = [n['value'] for n in historical if n.get('confirmed_customer_value') and
                           not any(r['history_slot'] == n['slot'] and r['same_role'] and r['exact'] and
                                   r['same_format'] and r['absolute_slot_match'] and template_compatible for r in rows)]
    query_codes = [n for n in query_stable if n['numeric_type'] in CODE_TYPES]
    history_codes = [n for n in history_stable if n['numeric_type'] in CODE_TYPES]
    code_sequence = lambda numbers: [(n['numeric_type'], n['value'], n.get('format', '#')) for n in numbers]
    # Missing codes and changed positions are also conflicts. A unique payer or
    # perfect semantic score cannot silently override an unresolved branch/code.
    code_alignment_complete = not (query_codes or history_codes) or (
        stable_sequence_matches and code_sequence(query_codes) == code_sequence(history_codes))
    code_conflicts = [r for r in rows if r['type'] in CODE_TYPES and
                      not (r['same_role'] and r['exact'] and r['same_format'] and stable_sequence_matches)]
    stable = [r for r in rows if r['type'] not in VARIABLE_TYPES]
    agreement = sum(r['exact'] and r['same_role'] for r in stable) / len(stable) if stable else 0
    return {'template_compatible': template_compatible, 'lexical_template_similarity': round(lexical, 4),
            'numeric_structure_similarity': round(structure, 4), 'ordered_comparison': rows,
            'stable_sequence_matches': stable_sequence_matches,
            'aligned_contracts': aligned_contracts, 'contract_conflict': contract_conflict,
            'code_conflicts': code_conflicts,
            'code_alignment_complete': code_alignment_complete,
            'query_codes': code_sequence(query_codes), 'history_codes': code_sequence(history_codes),
            'unresolved_confirmed_customer_tokens': unresolved_bound_ids,
            'confirmed_customer_tokens': bound_ids, 'ordered_agreement': round(agreement, 4)}
