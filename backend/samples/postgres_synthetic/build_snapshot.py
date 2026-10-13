"""Create a fully synthetic, E5-embedded pg_lab interchange snapshot; no network calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def build(output_dir: Path, model_directory: Path | None = None):
    from research_agent.hub_semantic import pack

    suite = json.loads(Path(__file__).with_name('suite.json').read_text(encoding='utf-8'))
    if suite['dataset_kind'] != 'synthetic':
        raise ValueError('synthetic_fixture_required')
    output_dir.mkdir(parents=True, exist_ok=True)
    database = output_dir / 'hub.sqlite3'
    if database.exists():
        raise FileExistsError('Use a new output directory; snapshots are not overwritten.')
    config, vectors = None, []
    if model_directory:
        from research_agent.hub_e5 import FILES, IDENTITY, MODEL, file_hash, runtime

        # Fail rather than invoking prepare(), which may download missing weights.
        if any(not (model_directory / name).is_file() or
               file_hash(model_directory / name) != expected for name, expected in FILES.items()):
            raise ValueError('complete_verified_local_e5_model_required')
        config = {'engine': 'e5', 'model': MODEL, 'model_digest': IDENTITY,
                  'model_directory': str(model_directory.resolve()), 'dimensions': 384,
                  'minimum_similarity': 0.84, 'strategy': 'all_chunks'}
        vectors = runtime(config).encode([d['title'] + '\n' + d['body'] for d in suite['documents']])
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript('''
            CREATE TABLE hub_sources(id TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            CREATE TABLE hub_documents(id TEXT PRIMARY KEY, source_id TEXT, title TEXT,
              body TEXT, published_at TEXT, url TEXT, metadata_json TEXT, fingerprint TEXT);
            CREATE TABLE hub_chunks(id INTEGER PRIMARY KEY, document_id TEXT, start INTEGER,
              end INTEGER, page INTEGER, text TEXT);
            CREATE TABLE hub_semantic(id INTEGER PRIMARY KEY, config_json TEXT);
            CREATE TABLE hub_vectors(chunk_id INTEGER, model_digest TEXT, content_hash TEXT,
              vector BLOB, PRIMARY KEY(chunk_id, model_digest));
        ''')
        conn.executemany('INSERT INTO hub_sources VALUES(?,?)',
                         [(s, int(s != 'synthetic_disabled'))
                          for s in sorted({d['source_id'] for d in suite['documents']})])
        for i, document in enumerate(suite['documents'], 1):
            body = document['body']
            body_hash = hashlib.sha256(body.encode()).hexdigest()
            conn.execute('INSERT INTO hub_documents VALUES(?,?,?,?,?,?,?,?)',
                         (document['id'], document['source_id'], document['title'], body,
                          document['date'], 'https://example.invalid/synthetic/' + document['id'],
                          json.dumps(document['metadata'], ensure_ascii=False), body_hash))
            conn.execute('INSERT INTO hub_chunks VALUES(?,?,?,?,?,?)',
                         (i, document['id'], 0, len(body), 1, body))
            if config:
                conn.execute('INSERT INTO hub_vectors VALUES(?,?,?,?)',
                             (i, config['model_digest'], body_hash, pack(vectors[i - 1])))
        if config:
            conn.execute('INSERT INTO hub_semantic VALUES(1,?)', (json.dumps(config),))
        conn.commit()
    (output_dir / 'queries.json').write_text(json.dumps(suite['queries'], ensure_ascii=False, indent=2),
                                           encoding='utf-8')
    return {'dataset_kind': 'synthetic', 'version': suite['version'],
            'document_count': len(suite['documents']), 'chunk_count': len(suite['documents']),
            'vector_count': len(vectors), 'query_count': len(suite['queries']),
            'answerable_queries': sum(bool(q['qrels']) for q in suite['queries']),
            'filter_noanswer_queries': sum(not q['qrels'] for q in suite['queries'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--model-directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir, args.model_directory)))


if __name__ == '__main__':
    main()
