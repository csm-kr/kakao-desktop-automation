#!/usr/bin/env python3
"""Read an already identified open chat, capturing only newly loaded row tails."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid


def capture(title, collector, navigator, output, max_pages=8):
    started = time.perf_counter()
    output.mkdir(parents=True, mode=0o700)
    output.chmod(0o700)
    merged = {}
    sources = []
    rows_requested = 0

    def invoke(arguments, name):
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=90)
        destination = output / name
        destination.write_text(result.stdout)
        destination.chmod(0o600)
        data = json.loads(result.stdout)
        if result.returncode != 0 or data.get('error'):
            raise ValueError(f"{name}: {data.get('error', 'command failed')}")
        return data

    def read_rows(limit, step):
        nonlocal rows_requested
        data = invoke([str(collector), title, str(limit)], f'capture-{step}.json')
        if data.get('ready') is not True or data.get('target_title') != title:
            raise ValueError('Exact title capture not ready')
        rows_requested += limit
        for row in data['rows']:
            index = row['row_index']
            previous = merged.get(index)
            if previous and previous['text_parts'] and row['text_parts'] and previous['body_sha256'] != row['body_sha256']:
                raise ValueError('Loaded row indices changed; do not merge into an unread digest')
            merged[index] = row
        path = output / f'capture-{step}.json'
        sources.append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        return data['rows_available']

    previous_count = read_rows(200, 0)
    markers = [i for i, row in merged.items() if '여기까지 읽었습니다.' in row['text_parts']]
    if len(markers) != 1:
        raise ValueError('Unread boundary not uniquely observed; do not label visible tail as unread')
    for step in range(1, max_pages + 1):
        nav = invoke([str(navigator), 'step', title, '1'], f'scroll-{step}.json')
        current_count = nav['rows_after']
        if current_count < previous_count:
            raise ValueError('Loaded row count decreased')
        # Two overlap rows include the previous placeholder and detect index shifts.
        limit = min(200, current_count - previous_count + 2)
        if current_count - previous_count + 2 > 200:
            raise ValueError('New row gap exceeds collector range')
        previous_count = read_rows(limit, step)
        if nav.get('after_scroll_value') == 1:
            final = invoke([str(navigator), 'scroll-info', title], 'bottom-check.json')
            if final.get('after_scroll_value') == 1 and final['rows_after'] == previous_count:
                break
    else:
        raise ValueError('Bottom not confirmed within page budget; partial captures retained')
    boundary = markers[0]
    if set(merged) != set(range(previous_count)):
        raise ValueError('Capture has a row gap')
    rows = [row for index, row in sorted(merged.items()) if index > boundary]
    data = {'room': title, 'boundary_row': boundary, 'rows': rows, 'sources': sources,
            'text_rows': sum(bool(row['text_parts']) for row in rows), 'bottom': 1,
            'scope': 'Observed unread divider to stable loaded bottom; text only; author/date unverified'}
    destination = output / 'unread-text.json'
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    destination.chmod(0o600)
    return {'capture': str(destination), 'rows': len(rows), 'text_rows': data['text_rows'],
            'collector_rows_requested': rows_requested, 'elapsed_ms': round((time.perf_counter()-started)*1000, 2), 'messages_sent': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--title', required=True, help='Exact already identified open chat title')
    parser.add_argument('--collector', type=Path, required=True, help='Collector path returned by bootstrap')
    parser.add_argument('--navigator', type=Path, required=True, help='Compiled kakao_room_nav.swift path')
    parser.add_argument('--output-dir', type=Path, default=Path.cwd() / 'var/unread')
    parser.add_argument('--max-pages', type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 20:
        parser.error('--max-pages must be 1...20')
    run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    result = capture(args.title, args.collector.resolve(), args.navigator.resolve(), args.output_dir.resolve() / run, args.max_pages)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
