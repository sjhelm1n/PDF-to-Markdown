from unicodedata import category, normalize


def upload_basename(filename: str) -> str:
    """Discard directory components supplied by any client platform."""
    return filename.replace('\\', '/').rsplit('/', 1)[-1]


def source_image_prefix(filename: str) -> str:
    basename = upload_basename(filename)
    stem = basename[:-4] if basename.lower().endswith('.pdf') else basename
    parts = []
    for char in normalize('NFC', stem):
        if char in '-_' or category(char)[0] in 'LMN':
            parts.append(char)
        elif parts and parts[-1] != '-':
            parts.append('-')

    prefix = ''.join(parts).strip('-_')
    prefix = prefix.encode('utf-8')[:100].decode('utf-8', errors='ignore').rstrip('-_')
    return prefix if any(char.isalnum() for char in prefix) else 'document'
