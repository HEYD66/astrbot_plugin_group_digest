"""SQLite queue tests + controlled protocol failure injection; no real QQ sends."""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from engine import Engine
from forwarding import send_card, same_message, OriginalMessageUnavailable, ForwardCardRejected
from settings import Settings
from storage import Store


def record(n, sender="a", group="g", text="ordinary", stamp=None):
    return dict(platform="p",group_id=group,group_name="Source group",message_id=str(1000+n),
                source_key="seq:"+str(n),sender_id=sender,sender_name="Source author",
                source_time=time.time()+n*0.001 if stamp is None else stamp,text=text,outline="",id=n)


class CardQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Store(Path(self.tmp.name)/"messages.sqlite3")
        self.s=Settings.parse({"recipients":["10001"],"keywords":["https://"],"summary_enabled":False})
        self.sent=[]
        async def card(target,records,metadata):
            self.sent.append((target,records,metadata))
            return {"message_id":"receipt"}
        async def text(*args):
            self.fail("Card delivery must not call the plain text sender")
        async def generate(*args):
            self.fail("Forwarding must not call a model")
        self.engine=Engine(self.db,lambda:self.s,generate,text,lambda _:None,card)

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def job(self):
        return dict(self.db.db.execute("select * from jobs order by id limit 1").fetchone())

    def add(self,n,**kwargs):
        r=record(n,**kwargs);r.pop("id")
        return self.db.ingest(r,self.s)

    async def test_context_same_group_same_author_and_early_finish_when_enough(self):
        t=time.time()-20
        self.add(1,stamp=t)
        self.add(2,sender="other",stamp=t+1)
        self.add(3,group="other",stamp=t+2)
        self.add(4,stamp=t+3)
        self.add(5,text="https://example.invalid",stamp=t+4)
        self.assertFalse(await self.engine.step("deliver"))
        self.add(6,stamp=t+5);self.add(7,stamp=t+6);self.add(8,stamp=t+7)
        self.assertTrue(await self.engine.step("deliver"))
        rows=self.sent[0][1]
        self.assertEqual([r["message_id"] for r in rows],["1001","1004","1005","1006","1007","1008"])
        self.assertEqual(self.job()["status"],"done")
        self.assertEqual(len(json.loads(self.job()["delivery_receipts"])),1)

    async def test_wait_deadline_sends_partial_without_future_or_other_sender(self):
        self.add(1,text="https://example.invalid")
        self.add(2,sender="other")
        job=self.job();self.db.finish_context(self.s,now=job["next_at"]+1)
        self.assertTrue(await self.engine.step("deliver"))
        self.assertEqual(len(self.sent[0][1]),1)

    async def test_zero_wait_sends_existing_previous_messages_without_delay(self):
        self.s=Settings.parse({**self.s.values,"forward_context_wait":0})
        self.add(1,stamp=time.time()-2);self.add(2,text="https://example.invalid",stamp=time.time()-1)
        self.assertTrue(await self.engine.step("deliver"))
        self.assertEqual(len(self.sent[0][1]),2)

    async def test_identical_event_and_repeated_matches_coalesce_in_one_window(self):
        r=record(1,text="https://example.invalid");r.pop("id")
        self.db.ingest(r,self.s);self.db.ingest(r,self.s)
        self.add(2,text="https://second.invalid")
        self.assertEqual(self.db.page("jobs")["total"],1)
        self.db.finish_context(self.s,now=self.job()["next_at"]+1)
        await self.engine.step("deliver")
        self.assertEqual(len(self.sent),1)
        self.assertEqual(len(set(self.sent[0][2]["matched_ids"])),2)

    async def test_collecting_window_survives_restart(self):
        self.add(1,text="https://example.invalid")
        deadline=self.job()["next_at"]
        self.db.close();self.db=Store(Path(self.tmp.name)/"messages.sqlite3");self.engine.store=self.db
        self.assertEqual(self.job()["status"],"collecting")
        self.assertEqual(self.job()["next_at"],deadline)
        self.db.finish_context(self.s,now=deadline+1)
        await self.engine.step("deliver")
        self.assertEqual(len(self.sent),1)
        self.assertFalse(await self.engine.step("deliver"))

    async def test_partial_multi_recipient_retry_skips_already_sent_card(self):
        self.s=Settings.parse({**self.s.values,"recipients":["10001","10002"],"forward_context_wait":0,"retry_limit":1})
        self.add(1,text="https://example.invalid")
        normal=self.engine.send_forward
        async def fail_second(target,rows,meta):
            if target.endswith("10002"):
                raise ConnectionError("do-not-log-this-secret")
            return await normal(target,rows,meta)
        self.engine.send_forward=fail_second
        await self.engine.step("deliver")
        self.assertEqual(self.job()["delivery_progress"],1)
        self.assertEqual(self.job()["status"],"failed")
        self.assertNotIn("secret",self.job()["last_error"])
        self.db.retry(self.job()["id"]);self.engine.send_forward=normal
        await self.engine.step("deliver")
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.job()["delivery_progress"],2)

    async def test_outside_time_window_not_included_and_no_history_forward_jobs(self):
        self.add(1,stamp=time.time()-900)
        self.add(2,text="https://example.invalid",stamp=time.time()-2)
        self.db.finish_context(self.s,now=self.job()["next_at"]+1)
        await self.engine.step("deliver")
        self.assertEqual(len(self.sent[0][1]),1)
        r=record(3,text="https://old.invalid");r.pop("id")
        self.db.ingest(r,self.s,history=True)
        self.assertEqual(self.db.page("jobs")["total"],1)

    async def test_target_change_pauses_collecting_card(self):
        self.add(1,text="https://example.invalid")
        self.s=Settings.parse({**self.s.values,"recipients":["10002"]})
        self.db.finish_context(self.s,now=self.job()["next_at"]+1)
        self.assertEqual(self.job()["status"],"collecting")
        self.assertFalse(await self.engine.step("deliver"))

    async def test_zero_wait_does_not_collect_later_arrivals(self):
        self.s=Settings.parse({**self.s.values,"forward_context_wait":0})
        self.add(1,text="https://example.invalid",stamp=time.time()-2)
        self.add(2,stamp=time.time()-1)
        await self.engine.step("deliver")
        self.assertEqual(len(self.sent[0][1]),1)

    async def test_legacy_text_job_keeps_existing_plan_and_mode(self):
        self.s=Settings.parse({**self.s.values,"forward_format":"text"})
        self.add(1,text="https://example.invalid")
        self.s=Settings.parse({**self.s.values,"forward_format":"card"})
        calls=[]
        async def text(target,body):calls.append(body);return True
        self.engine.send=text
        await self.engine.step("deliver")
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.sent),0)
        self.assertEqual(self.job()["delivery_mode"],"text")


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def source(self,n=1):
        r=record(n,sender="200",stamp=100)
        return {**r,"id":n}

    def raw(self,r):
        return {"message_id":r["message_id"],"message_type":"group","group_id":r["group_id"],
                "user_id":r["sender_id"],"real_seq":r["source_key"].split(":")[1],"time":r["source_time"]}

    async def test_native_reference_nodes_and_info_header_with_valid_receipt(self):
        r=self.source();calls=[]
        async def call(action,params):
            calls.append((action,params))
            if action=="get_msg":return self.raw(r)
            if action=="get_login_info":return {"user_id":123,"nickname":"Actual bot"}
            if action=="send_private_forward_msg":return {"message_id":567,"res_id":"not-persisted"}
            self.fail(action)
        meta=dict(job_id=1,hits=["https://"],matched_ids=[1])
        receipt=await send_card(call,call,"10001",[r],meta)
        self.assertEqual(receipt,{"message_id":"567"})
        nodes=calls[-1][1]["messages"]
        self.assertEqual(nodes[1],{"type":"node","data":{"id":r["message_id"]}})
        self.assertEqual(nodes[0]["data"]["user_id"],"123")

    async def test_reused_wrong_id_not_forwarded_and_history_rebinds_native_sequence(self):
        r=self.source()
        async def call(action,params):
            if action=="get_msg":return {**self.raw(r),"group_id":"wrong"}
            if action=="get_group_msg_history":return {"messages":[{**self.raw(r),"message_id":"new-short-id"}]}
            if action=="get_login_info":return {"user_id":123}
            if action=="send_private_forward_msg":
                self.assertEqual(params["messages"][1]["data"]["id"],"new-short-id")
                return {"message_id":567}
        await send_card(call,call,"10001",[r],dict(job_id=1,hits=[],matched_ids=[1]))

    async def test_missing_original_fails_without_fabricated_text_card(self):
        r=self.source();sent=[]
        async def call(action,params):
            if action=="get_msg":raise LookupError()
            if action=="get_group_msg_history":return {"messages":[]}
            sent.append(action)
        with self.assertRaises(OriginalMessageUnavailable):
            await send_card(call,call,"10001",[r],dict(job_id=1,hits=[],matched_ids=[1]))
        self.assertEqual(sent,[])

    async def test_wrong_sender_mixed_context_rejected(self):
        r=self.source();r2={**r,"id":2,"sender_id":"other"}
        async def call(*args):self.fail("Must validate before any protocol call")
        with self.assertRaises(ValueError):
            await send_card(call,call,"10001",[r,r2],dict(job_id=1,hits=[],matched_ids=[1]))

    async def test_missing_receipt_not_marked_success(self):
        r=self.source()
        async def call(action,params):
            if action=="get_msg":return self.raw(r)
            if action=="get_login_info":return {"user_id":123}
            if action=="send_private_forward_msg":return {"status":"ok"}
        with self.assertRaises(ForwardCardRejected):
            await send_card(call,call,"10001",[r],dict(job_id=1,hits=[],matched_ids=[1]))


if __name__=="__main__":unittest.main()
