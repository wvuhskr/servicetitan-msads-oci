"""Real Worker handler/normalizer output consumed by the real Python build."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from st_msads_oci.build import build
from tests.conftest import make_settings


def test_authenticated_capture_to_python_build_and_conflict_fallback(tmp_path):
    root = Path(__file__).parents[1]
    payload = json.loads((root / 'examples/sample-input.json').read_text())
    email = payload['jobs'][0]['email']
    # Only the storage/network boundary is replaced. Both implementations and
    # their normalizers run unchanged against the same synthetic contact.
    script = '''import worker from './worker/src/worker.js';
import fs from 'node:fs';
const email=JSON.parse(fs.readFileSync(0,'utf8'));
Date.now=()=>Date.parse('2026-07-06T12:00:00Z');
const db=new Map();
const env={ALLOWED_ORIGIN:'https://shop.example', CAPTURE_BEARER:'capture-test', OCI_BEARER:'export-test',
OCI:{async put(name,value,{metadata}){db.set(name,{name,metadata});},
async list(){return {keys:[...db.values()],list_complete:true};}}};
const maps=[];
for(const click of ['first-click','conflicting-click']){
 const response=await worker.fetch(new Request('https://worker.example/c',{method:'POST',
 headers:{Authorization:'Bearer capture-test'},
 body:JSON.stringify({kind:'form',event_id:click,msclkid:click,email,ts:'2026-07-01T12:00:00Z'})}),env);
 if(response.status!==204)throw Error('capture failed');
 const map=await worker.fetch(new Request('https://worker.example/map',{headers:{Authorization:'Bearer export-test'}}),env);
 maps.push(await map.json());
}
console.log(JSON.stringify(maps));'''
    maps = json.loads(subprocess.run(['node', '--input-type=module', '-e', script], cwd=root,
                                    input=json.dumps(email), capture_output=True, text=True, check=True).stdout)
    results = []
    for index, mapping in enumerate(maps):
        directory = tmp_path / str(index); directory.mkdir()
        infile, mapfile = directory / 'input.json', directory / 'map.json'
        infile.write_text(json.dumps(payload)); mapfile.write_text(json.dumps(mapping))
        result = build(directory, infile, make_settings(initial_watermark='2026-06-01T00:00:00Z'),
                       {'OCI_MAP_FILE': str(mapfile), 'OCI_DOWNLOADS_DIR': str(directory / 'none')},
                       datetime(2026, 7, 6, 12, tzinfo=timezone.utc), notify=False)
        results.append(result)
    assert results[0]['tier_a'] == 1 and results[0]['tier_b'] == 1
    assert 'first-click' in (tmp_path / '0/output/oci-clickid.csv').read_text()
    assert results[1]['tier_a'] == 0 and results[1]['tier_b'] == 2
    assert 'conflicting-click' not in (tmp_path / '1/output/oci-clickid.csv').read_text()
