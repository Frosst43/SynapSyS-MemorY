#!/usr/bin/env python3
"""One local release-component validation, with explicit blocked/manual gates.

No dependency installation, sudo, network calls or user-project writes.
Evidence is stored in a new private temporary directory outside the repo.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    evidence = Path(tempfile.mkdtemp(prefix='symem-release-validation-'))
    env = os.environ.copy()
    env['PYTHONPATH'] = str(root/'src')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.pop('SYMEM_ADMIN_KEY', None)
    env.pop('SYMEM_SESSION_TOKEN', None)
    env.pop('SYMEM_SESSION_TOKEN_FILE', None)
    stages = []
    def run(name: str, args: list[str], available: bool = True, reason: str = '') -> None:
        if not available:
            stages.append(dict(name=name, status='BLOCKED', reason=reason))
            print(f'BLOCKED: {name}: {reason}')
            return
        started = time.monotonic()
        path = evidence/(name+'.log')
        try:
            with path.open('w', encoding='utf-8') as out:
                result = subprocess.run([sys.executable, *args], cwd=root, env=env,
                                        stdout=out, stderr=subprocess.STDOUT,
                                        timeout=120, check=False)
            code, status = result.returncode, 'PASS' if result.returncode == 0 else 'FAIL'
        except subprocess.TimeoutExpired:
            code, status = 124, 'FAIL'
        stages.append(dict(name=name, status=status, exit_code=code,
                           seconds=round(time.monotonic()-started, 3), log=str(path)))
        print(f'{status}: {name}; log={path}')
    run('unit', ['-m','unittest','discover','-s','tests','-v'])
    run('mcp', ['scripts/verify_mcp_stdio.py'], importlib.util.find_spec('mcp') is not None,
        'Install the optional MCP extra in this virtual environment')
    run('sandbox_broker', ['scripts/verify_sandbox_broker_bridge.py'],
        sys.platform == 'linux' and os.access('/usr/bin/bwrap', os.X_OK),
        'Linux and /usr/bin/bwrap are required; no unisolated fallback')
    passed = all(s['status']=='PASS' for s in stages)
    summary = dict(
        version='1.0.0rc1', python=platform.python_version(), platform=platform.platform(),
        component_tests_passed=passed, stable_release_ready=False, stages=stages,
        manual_gates=[
            'Actual installed AI client, memory adapter and approved runtime inside sandbox',
            'Trusted project switch must stop/reap old sandbox before granting new project',
            'Target-machine OS identities and private credential provisioning reviewed',
        ],
        real_user_projects_modified=False,
    )
    (evidence/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print('EVIDENCE: '+str(evidence/'summary.json'))
    print('COMPONENTS_PASS; STABLE_RELEASE_BLOCKED' if passed else 'COMPONENTS_NOT_VALIDATED')
    return 0 if passed else 2


if __name__ == '__main__': raise SystemExit(main())
