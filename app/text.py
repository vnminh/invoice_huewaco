"""Text folding shared by parsers; no application configuration or model loading."""
import unicodedata


def fold(text):
    value = unicodedata.normalize('NFKD', str(text)).lower().replace('đ', 'd')
    return ''.join(char for char in value if not unicodedata.combining(char))
