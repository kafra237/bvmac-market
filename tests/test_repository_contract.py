from __future__ import annotations
import json
import hashlib
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'web'

class RepositoryContractTests(unittest.TestCase):
    def test_public_structure_is_functional_and_small(self):
        for folder in ('web','backend/api','backend/jobs','pipeline','database','ml','operations','docs/en','docs/fr','tests'):
            self.assertTrue((ROOT/folder).is_dir(),folder)
        self.assertEqual(sorted(p.name for p in ROOT.rglob('*.sh')),['checking.sh','deploy.sh'])
        for legacy in ('VERSION','update.sh','run_pipeline.sh','old','CHECKSUMS.sha256','DEPLOYMENT_STANDARD_IMMUTABLE.md'):
            self.assertFalse((ROOT/legacy).exists(),legacy)

    def test_no_private_deployment_identity_in_source(self):
        bad=[]
        patterns=(r'duckdns\.org', r'C:\\Users\\')
        for p in ROOT.rglob('*'):
            if not p.is_file() or p.suffix.lower() in {'.png','.joblib','.pyc'} or '.git' in p.parts or '__pycache__' in p.parts or p.name=='test_repository_contract.py': continue
            text=p.read_text(encoding='utf-8',errors='ignore')
            for pat in patterns:
                if re.search(pat,text,re.I): bad.append((p.relative_to(ROOT).as_posix(),pat))
        self.assertEqual(bad,[])

    def test_release_token_is_runtime_generated(self):
        self.assertEqual(json.loads((WEB/'version.json').read_text())['release'],'__BVMAC_RELEASE__')
        self.assertIn("const RELEASE='__BVMAC_RELEASE__'",(WEB/'pwa.js').read_text())
        self.assertIn("const RELEASE='__BVMAC_RELEASE__'",(WEB/'sw.js').read_text())
        for name in ('app.html','app-feeds.html','app-analysis.html','app-portfolios.html','auth.html','index.html','information.html','demo.html'):
            self.assertIn('__BVMAC_RELEASE__',(WEB/name).read_text(encoding='utf-8'),name)

    def test_canonical_authenticated_routes(self):
        for p in list(WEB.glob('*.html'))+list(WEB.glob('*.js')):
            text=p.read_text(encoding='utf-8',errors='ignore')
            self.assertNotRegex(text,r'/app\?view=[^\s"\']+\?')
        shell=(WEB/'shell.js').read_text(encoding='utf-8')
        for view in ('home','overview','stories','stocks','funds','index','radar','watchlist','data','feeds','analysis','portfolios'):
            self.assertIn(view,shell)

    def test_pwa_does_not_cache_private_api(self):
        sw=(WEB/'sw.js').read_text(encoding='utf-8')
        self.assertIn("url.pathname.startsWith('/api/')",sw)
        self.assertIn("cache:'no-store'",sw)
        self.assertIn('SKIP_WAITING',sw)
        pwa=(WEB/'pwa.js').read_text(encoding='utf-8')
        self.assertIn('version.json',pwa)
        self.assertIn('registration.update()',pwa)

    def test_ml_and_etl_boundaries_are_explicit(self):
        self.assertTrue((ROOT/'pipeline/run.py').is_file())
        self.assertTrue((ROOT/'ml/MODEL_CARD.md').is_file())
        self.assertTrue((ROOT/'ml/VALIDATED_TARGETS.csv').is_file())
        deploy=(ROOT/'deploy.sh').read_text(encoding='utf-8')
        self.assertIn('ExecStartPost=-$APP_CURRENT/.venv/bin/python $APP_CURRENT/ml/infer.py',deploy)


    def test_historical_etl_is_preserved(self):
        expected={
            'bvmac_downloader.py':'5e4fc8179237ce727b1d8692abeb2f3d24833730ce3ccbbd823c8256d2c898b7',
            'bvmac_extract.py':'baee7acd3f12802f60de4dcf042c017d2eb60f0300f608e24d0e8e4e156c22e1',
        }
        for name,digest in expected.items():
            actual=hashlib.sha256((ROOT/'pipeline'/name).read_bytes()).hexdigest()
            self.assertEqual(actual,digest,name)

    def test_targeted_weekly_campaign_regression(self):
        comm=(ROOT/'backend/api/admin_communications.py').read_text(encoding='utf-8')
        admin=(ROOT/'backend/api/admin_module.py').read_text(encoding='utf-8')
        stat=(WEB/'stat-app.js').read_text(encoding='utf-8')
        self.assertNotIn('conn.executemany',comm)
        self.assertIn('unnest(%s::bigint[])',comm)
        self.assertIn("@router.post('/api/stat/communications/weekly/send')",admin)
        self.assertIn("'/api/stat/communications/weekly/send'",stat)

    def test_documentation_exists_in_both_languages(self):
        self.assertTrue((ROOT/'README.md').is_file()); self.assertTrue((ROOT/'README.fr.md').is_file())
        en=list((ROOT/'docs/en').glob('*.md')); fr=list((ROOT/'docs/fr').glob('*.md'))
        self.assertGreaterEqual(len(en),10); self.assertEqual(len(en),len(fr))

if __name__=='__main__': unittest.main(verbosity=2)
