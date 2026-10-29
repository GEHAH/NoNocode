"""Isolated filesystem read worker; killed if a network filesystem stalls."""
import json
from pathlib import Path
import sys

MAX_READ_BYTES = 4_000_000


def main():
    path = Path(sys.argv[1])
    offset, limit = int(sys.argv[2]), int(sys.argv[3])
    try:
        if not path.is_file():
            raise ValueError(f'file not found or not a regular file: {path}')
        with path.open('rb') as stream:
            raw = stream.read(MAX_READ_BYTES + 1)
        if len(raw) > MAX_READ_BYTES:
            raise ValueError('file exceeds the 4 MB read limit; use Grep or split the file')
        lines = raw.decode('utf-8').splitlines()
        output = '\n'.join(f'{i + offset + 1}\t{line}' for i, line in enumerate(lines[offset:offset+limit]))
        result = {'output': output, 'mtime_ns': path.stat().st_mtime_ns, 'resolved': str(path.resolve())}
    except Exception as exc:
        result = {'output': f'Error reading file: {exc}', 'is_error': True}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
