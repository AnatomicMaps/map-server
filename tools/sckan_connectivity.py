#===============================================================================
#
#  Flatmap viewer and annotation tools
#
#  Copyright (c) 2019-21  David Brooks
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
#===============================================================================

import json
import logging
import os
from pathlib import Path

from tqdm import tqdm

#===============================================================================

from mapknowledge import KnowledgeStore

#===============================================================================

def load(args):
    scicrunch_key = os.environ.get('SCICRUNCH_API_KEY')
    if scicrunch_key is None:
        logging.error('Undefined SCICRUNCH_API_KEY -- cannot load SCKAN knowledge')
        exit(1)

    store = KnowledgeStore(
        store_directory=args.store_directory,
        knowledge_base=args.knowledge_store,
        sckan_version=args.sckan,
        scicrunch_key=scicrunch_key,
        use_sckan=True
        )
    if store.db is None:
        raise IOError(f'Unable to open knowledge store {args.store_directory}/{args.knowledge_store}')

    knowledge_source = store.source
    logging.info(f'Loading SCKAN NPO connectivity for source `{knowledge_source}`')

    store.db.execute('delete from knowledge where source=?', (knowledge_source,))
    store.db.execute('delete from connectivity_nodes where source=?', (knowledge_source,))
    store.db.commit()

    paths = store.connectivity_paths()
    progress_bar = tqdm(total=len(paths),
        unit='path', ncols=80,
        bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}')

    path_count = 0
    for path in paths:
        store.entity_knowledge(path, source=knowledge_source)
        progress_bar.update(1)
        path_count += 1

    store.close()
    progress_bar.close()
    logging.info(f'Loaded connectivity for {path_count} paths for `{knowledge_source}`')

    # Having loaded connectivity we can now save it to JSON
    if args.save_json:
        args.source = knowledge_source
        extract(args)

#===============================================================================

def extract(args):
    knowledge_source = args.source
    store = KnowledgeStore(
        store_directory=args.store_directory,
        knowledge_base=args.knowledge_store,
        read_only=True,
        knowledge_source=knowledge_source)
    if store.db is None:
        raise IOError(f'Unable to open knowledge store {args.store_directory}/{args.knowledge_store}')
    knowledge_source = store.source
    if knowledge_source is None:
        raise ValueError(f'No valid knowledge sources in {args.store_directory}/{args.knowledge_store}')

    saved_knowledge = {
        'source': knowledge_source,
        'knowledge': []
    }
    for row in store.db.execute('select entity, knowledge from knowledge where source=?', (knowledge_source,)).fetchall():
        knowledge = json.loads(row[1])
        knowledge['id'] = row[0]
        saved_knowledge['knowledge'].append(knowledge)

    json_file = Path(args.store_directory) / f'{knowledge_source}.json'
    with open(json_file, 'w') as fp:
        json.dump(saved_knowledge, fp, indent=4)
    logging.info(f"Saved {len(saved_knowledge['knowledge'])} records for `{knowledge_source}` to `{json_file}`")

#===============================================================================

def restore(args):
    with open(args.json_file) as fp:
        saved_knowledge = json.load(fp)
    store = KnowledgeStore(
        store_directory=args.store_directory,
        knowledge_base=args.knowledge_store,
        use_sckan=False)
    if store.db is None:
        raise IOError(f'Unable to open knowledge store {args.store_directory}/{args.knowledge_store}')

    knowledge_source = saved_knowledge['source']
    store.clean_connectivity(knowledge_source)
    store.db.execute('delete from knowledge where source=?', (knowledge_source,))
    store.db.execute('delete from connectivity_nodes where source=?', (knowledge_source,))
    for knowledge in saved_knowledge['knowledge']:
        entity = knowledge['id']
        store.db.execute('insert into knowledge (source, entity, knowledge) values (?, ?, ?)',
                                             (knowledge_source, entity, json.dumps(knowledge)))
        # Save label and references in their own tables
        if 'label' in knowledge:
            store.db.execute('replace into labels values (?, ?)', (entity, knowledge['label']))
        if 'references' in knowledge:
            references = knowledge.get('references', [])
            store.db.execute('delete from publications where source=? and entity=?', (knowledge_source, entity, ))
            store.db.executemany('insert into publications(source, entity, publication) values (?, ?)',
                ((knowledge_source, entity, reference) for reference in references))
        if 'connectivity' in knowledge:
            seen_nodes = set()
            for edge in knowledge['connectivity']:
                for node in edge:
                    node = (node[0], tuple(node[1]))
                    if node not in seen_nodes:
                        seen_nodes.add(node)
                        store.db.execute('insert into connectivity_nodes (source, node, path) values (?, ?, ?)',
                                                                    (knowledge_source, json.dumps(node), entity))
    store.db.commit()

    logging.info(f"Restored {len(saved_knowledge['knowledge'])} records for `{knowledge_source}` from `{args.json_file}`")

#===============================================================================

def info(args):
    store = KnowledgeStore(
        store_directory=args.store_directory,
        knowledge_base=args.knowledge_store,
        read_only=True,
        use_sckan=False)
    for source in store.knowledge_sources():
        print(source)

#===============================================================================

DEFAULT_STORE = 'knowledgebase.db'

if __name__ == '__main__':
#=========================

    import argparse

    parser = argparse.ArgumentParser(description='Load, extract, and restore SCKAN NPO connectivity knowledge in a local knowledge store.')
    parser.add_argument('--store-directory', required=True, help='Directory containing a knowledge store')
    parser.add_argument('--knowledge-store', default=DEFAULT_STORE, help=f'Name of knowledge store file. Defaults to `{DEFAULT_STORE}`')
    parser.add_argument('-q', '--quiet', action='store_true', help='Suppress INFO log messages')

    subparsers = parser.add_subparsers(title='commands', required=True)

    parser_load = subparsers.add_parser('load', help='Load connectivity knowledge from SCKAN NPO into a local knowledge store.')
    parser_load.add_argument('--sckan', help='SCKAN release identifier; defaults to latest available version of SCKAN')
    parser_load.add_argument('--save-json', action='store_true', help='Optionally save connectivity knowledge as JSON in the store directory.')
    parser_load.set_defaults(func=load)

    parser_extract = subparsers.add_parser('extract', help='Save connectivity knowledge from a local store as JSON in the store directory.')
    parser_extract.add_argument('--source', help='Knowledge source to extract; defaults to the most recent source in the store.')
    parser_extract.set_defaults(func=extract)

    parser_restore = subparsers.add_parser('restore', help='Restore connectivity knowledge to a local store from JSON.')
    parser_restore.add_argument('json_file', metavar='JSON_FILE', help='File to load connectivity knowledge from.')
    parser_restore.set_defaults(func=restore)

    parser_info = subparsers.add_parser('info', help='List knowledge sources in a local store.')
    parser_info.set_defaults(func=info)

    args = parser.parse_args()
    if not args.quiet:
        logging.basicConfig(level=logging.INFO)
    args.func(args)

#===============================================================================