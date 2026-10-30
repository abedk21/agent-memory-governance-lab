import os
import tempfile
import unittest
from memory_lab import Actor, MemoryStore, MemoryPolicyError

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.clock=[100.0]
        self.db=MemoryStore(now=lambda: self.clock[0])
        self.a=Actor("t1","user1","s1")
        self.other_user=Actor("t1","user2","s1")
        self.other_tenant=Actor("t2","user1","s1")
    def tearDown(self): self.db.close()
    def put(self, **kw): return self.db.remember(self.a, "writing_style", "Prefer clear examples.", **kw)
    def test_session_default(self):
        self.assertEqual(self.put()["result"],"created")
        self.assertEqual(len(self.db.recall(self.a)),1)
    def test_cross_tenant(self):
        self.put();self.assertEqual(self.db.recall(self.other_tenant),[])
    def test_cross_user(self):
        self.put();self.assertEqual(self.db.recall(self.other_user),[])
    def test_session_scoping(self):
        self.put();self.assertEqual(self.db.recall(Actor("t1","user1","s2")),[])
    def test_durable_scoping(self):
        self.put(scope="durable",consent=True)
        self.assertEqual(len(self.db.recall(Actor("t1","user1","s2"))),1)
    def test_durable_requires_consent(self):
        with self.assertRaises(MemoryPolicyError): self.put(scope="durable")
    def test_block_retrieval_promote(self):
        with self.assertRaises(MemoryPolicyError): self.put(source="retrieved_document")
    def test_block_tool_promote(self):
        with self.assertRaises(MemoryPolicyError): self.put(source="tool_output")
    def test_reject_secret(self):
        with self.assertRaises(MemoryPolicyError): self.db.remember(self.a,"api_credentials","api_key=SECRET_VALUE")
    def test_reject_bearer(self):
        with self.assertRaises(MemoryPolicyError): self.db.remember(self.a,"api_credentials","Bearer: an-unsafe-token")
    def test_length_bound(self):
        with self.assertRaises(MemoryPolicyError): self.db.remember(self.a,"long_value","a"*601)
    def test_bad_slot(self):
        with self.assertRaises(MemoryPolicyError): self.db.remember(self.a,"../escape","foo")
    def test_bad_actor(self):
        with self.assertRaises(MemoryPolicyError): Actor("t","u","../bad")
    def test_duplicate_noop(self):
        mid=self.put()["id"]
        self.assertEqual(self.put(),{"id":mid,"result":"unchanged"})
        self.assertEqual(self.db.stats()["active"],1)
    def test_conflict_supersession(self):
        first=self.put()["id"]
        newer=self.db.remember(self.a,"writing_style","Prefer concise answers.")
        self.assertEqual(newer["result"],"updated")
        self.assertEqual([m["text"] for m in self.db.recall(self.a)],["Prefer concise answers."])
        self.assertEqual(len(self.db.export(self.a)),2)
        self.assertEqual(self.db.export(self.a)[0]["status"],"superseded")
    def test_different_scopes_do_not_conflict(self):
        self.put();self.put(scope="durable",consent=True)
        self.assertEqual(len(self.db.recall(self.a)),2)
    def test_query(self):
        self.put(); self.db.remember(self.a,"current_task","Debug OAuth permissions.")
        self.assertEqual(self.db.recall(self.a,"oauth")[0]["slot"],"current_task")
    def test_query_excludes_no_overlap(self):
        self.put();self.assertEqual(self.db.recall(self.a,"kubernetes"),[])
    def test_budget(self):
        self.put();self.assertEqual(self.db.recall(self.a,word_budget=2),[])
    def test_budget_count_is_reported(self):
        self.put();self.assertEqual(self.db.recall(self.a)[0]["word_cost"],3)
    def test_recalled_content_untrusted(self):
        self.put();self.assertEqual(self.db.recall(self.a)[0]["trust"],"untrusted_context")
    def test_expiration(self):
        self.put(ttl_seconds=3);self.clock[0]+=3
        self.assertEqual(self.db.recall(self.a),[])
    def test_pruning_removes_data(self):
        self.put(ttl_seconds=1);self.clock[0]+=2
        self.assertEqual(self.db.prune_expired(),1)
        self.assertEqual(self.db.export(self.a),[])
    def test_session_ttl_limit(self):
        with self.assertRaises(MemoryPolicyError): self.put(ttl_seconds=86401)
    def test_durable_ttl_limit(self):
        with self.assertRaises(MemoryPolicyError): self.put(scope="durable",consent=True,ttl_seconds=366*86400)
    def test_forget_by_id(self):
        mid=self.put()["id"]
        self.assertEqual(self.db.forget(self.a,memory_id=mid),1)
        self.assertEqual(self.db.recall(self.a),[])
    def test_forget_slot_includes_history(self):
        self.put();self.db.remember(self.a,"writing_style","New setting.")
        self.assertEqual(self.db.forget(self.a,slot="writing_style"),2)
    def test_forget_cannot_access_other_user(self):
        mid=self.put()["id"]
        self.assertEqual(self.db.forget(self.other_user,memory_id=mid),0)
        self.assertEqual(len(self.db.recall(self.a)),1)
    def test_forget_cannot_access_other_tenant(self):
        mid=self.put()["id"]
        self.assertEqual(self.db.forget(self.other_tenant,memory_id=mid),0)
    def test_forget_requires_exactly_one_selector(self):
        with self.assertRaises(MemoryPolicyError): self.db.forget(self.a)
        with self.assertRaises(MemoryPolicyError): self.db.forget(self.a,memory_id=2,slot="x")
    def test_audit_does_not_capture_content(self):
        self.put(); rows=self.db.db.execute("SELECT * FROM audit").fetchall()
        self.assertNotIn("clear examples",str([tuple(r) for r in rows]))
    def test_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"mem.db")
            with MemoryStore(path,now=lambda:100.0) as db: db.remember(self.a,"durable_note","Retain this note.",scope="durable",consent=True)
            with MemoryStore(path,now=lambda:101.0) as db: self.assertEqual(len(db.recall(self.a)),1)
    def test_word_budget_validation(self):
        with self.assertRaises(MemoryPolicyError): self.db.recall(self.a,word_budget=0)
    def test_expired_does_not_supersede_new(self):
        self.put(ttl_seconds=1);self.clock[0]+=2
        self.assertEqual(self.db.remember(self.a,"writing_style","A newer valid note.")["result"],"created")

if __name__=="__main__": unittest.main()
