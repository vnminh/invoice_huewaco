"""Bounded row-error summaries; full reports may be streamed to working files."""
from collections import Counter


ROW_DATA_ERRORS = (ValueError, TypeError, ArithmeticError, LookupError)


class RowErrors:
    sample_limit = 50

    def __init__(self, on_error=None, file=''):
        self.on_error = on_error
        self.file = file
        self.count = 0
        self.samples = []
        self.sheet_counts = Counter()

    def record(self, sheet, row_index, error, stage='read', row_position=None):
        # Database exceptions otherwise include SQL and an entire parameter dump.
        original = getattr(error, 'orig', None)
        diagnostic = getattr(original, 'diag', None)
        message = getattr(diagnostic, 'message_primary', None) or str(error)
        issue = {'file': self.file, 'sheet': sheet or 'CSV', 'row_index': row_index,
                 'row_position': row_position, 'stage': stage, 'error': message[:1000],
                 'error_type': type(error).__name__}
        self.count += 1
        self.sheet_counts[issue['sheet']] += 1
        if len(self.samples) < self.sample_limit:
            self.samples.append(issue)
        if self.on_error:
            self.on_error(issue)

    def summary(self):
        return {'row_error_count': self.count, 'row_errors': list(self.samples),
                'error_sheet_counts': dict(self.sheet_counts)}


def prepare_rows(core, rows, errors, check_cancel, chunk_size=32):
    """Retry a data-invalid encoder batch one row at a time to isolate bad rows."""
    failed = set()
    for start in range(0, len(rows), chunk_size):
        check_cancel()
        chunk = rows[start:start + chunk_size]
        try:
            core.prepare_batch([row.raw for row in chunk])
        except ROW_DATA_ERRORS:
            for row in chunk:
                check_cancel()
                try:
                    core.prepare_batch([row.raw])
                except ROW_DATA_ERRORS as error:
                    errors.record(row.sheet, row.row_index, error, stage='prepare')
                    failed.add(id(row))
    return failed
