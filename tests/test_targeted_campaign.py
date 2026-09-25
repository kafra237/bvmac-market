import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

def load_create_campaign():
    tree=ast.parse((ROOT/'backend/api/admin_communications.py').read_text(encoding='utf-8'))
    fn=next(n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='create_campaign')
    mod=ast.Module(body=[fn],type_ignores=[]); ast.fix_missing_locations(mod)
    ns={}
    exec(compile(mod,'admin_communications.py','exec'),ns)
    return ns['create_campaign']

class Cursor:
    def __init__(self,row=None): self.row=row
    def fetchone(self): return self.row

class ConnectionOnlyExecute:
    """Double volontairement sans executemany(): reproduit le contrat utilisé par le patch."""
    def __init__(self): self.calls=[]; self.commits=0
    def execute(self,query,params=()):
        self.calls.append((query,params))
        if 'RETURNING campaign_id' in query: return Cursor((77,))
        return Cursor()
    def commit(self): self.commits+=1

class TargetedCampaignRegressionTests(unittest.TestCase):
    def test_selected_weekly_campaign_uses_connection_execute_only(self):
        create_campaign=load_create_campaign(); conn=ConnectionOnlyExecute()
        cid=create_campaign(conn,kind='weekly_email',user_ids=[4,2,4],target_url='/app?view=radar',requested_by='admin')
        self.assertEqual(cid,77); self.assertEqual(conn.commits,1); self.assertEqual(len(conn.calls),2)
        self.assertIn('unnest(%s::bigint[])',conn.calls[1][0])
        self.assertEqual(conn.calls[1][1],(77,[2,4]))
    def test_empty_selection_is_rejected_before_database_write(self):
        create_campaign=load_create_campaign(); conn=ConnectionOnlyExecute()
        with self.assertRaises(ValueError): create_campaign(conn,kind='weekly_email',user_ids=[])
        self.assertEqual(conn.calls,[])

if __name__=='__main__': unittest.main(verbosity=2)
