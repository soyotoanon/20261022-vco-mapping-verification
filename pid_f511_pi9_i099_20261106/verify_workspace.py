"""Check preserved F511 source and report candidate source changes; no hardware I/O."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'baseline_manifest.json').read_text(encoding='utf-8'))
expected = manifest['original_files_sha256']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

baseline_mismatches = [name for name, digest in expected.items()
                       if sha(root / 'baseline_f511' / name) != digest]
candidate_changes = [name for name, digest in expected.items()
                     if sha(root / 'candidate' / name) != digest]
result = {
    'baseline_commit': manifest['baseline_commit'],
    'baseline_file_count': len(expected),
    'baseline_intact': not baseline_mismatches,
    'baseline_mismatches': baseline_mismatches,
    'candidate_modified_or_missing_baseline_files': candidate_changes,
    'hardware_io_performed_by_this_check': False,
}
(root / 'workspace_verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, indent=2))
if baseline_mismatches:
    raise SystemExit(1)
