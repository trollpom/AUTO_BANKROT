"""Offline contracts and complete admin API flow; no keys or external calls required."""
import base64
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

# Configure isolation BEFORE app imports: do not touch a developer's DB or workers.
TEMP = tempfile.TemporaryDirectory(prefix="car_lot_pilot-test-")
os.environ.update(EFRSB_DATA=TEMP.name, EFRSB_DB=str(Path(TEMP.name) / "efrsb.db"),
                  EMAIL_AUTO_SEND="0", EMAIL_AUTO_CHECK="0", TORGI_AUTO_SCRAPE="0", EMAIL2_ENABLED="0",
                  ADMIN_PASSWORD="offline-test-only", DEEPSEEK_API_KEY="")

from PIL import Image
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app import db
from app.api.main import app
from app.analysis import evidence, finance, market, service, store
from app.analysis.provider import DeepSeekClient, ProviderError
from app.analysis.schemas import AnalysisRequest, Comparable, ConditionOutput, Costs, Inputs, MarketOutput, Vehicle
from app.analysis.settings import AnalysisSettings

NOW = datetime.now(timezone.utc)
VEHICLE = Vehicle(make="Toyota", model="Camry", year=2015)


def comparable(number=0, price="1000000", **changes):
    values = dict(url=f"https://auto.ru/cars/used/sale/toyota/camry/{10000000+number}-a/", title="Toyota Camry 2015",
                  make="Toyota", model="Camry", year=2015, price_rub=price, observed_at=NOW,
                  listing_status="active", damaged=False)
    values.update(changes)
    return Comparable(**values)


def costs(**changes):
    values = {name: "0" for name in Costs.model_fields}
    values.update(changes)
    return Costs(**values)


class FinancialTests(unittest.TestCase):
    def test_exact_scenario_and_bid_ceiling(self):
        m = {"status":"estimated", "low_rub":1000000,"median_rub":1100000,"high_rub":1200000}
        c = costs(transport_rub="10000", repair_low_rub="20000",repair_base_rub="30000",repair_high_rub="40000",
                  buyer_fee_pct="2",selling_fee_pct="1",resale_discount_pct="10",capital_rate_pct_year="12",
                  holding_months="6",target_profit_rub="100000")
        result = finance.calculate(m, c, Decimal("600000"))
        low = result["scenarios"][0]
        # 900k * .99 - (600k * 1.02 + 10k + 40k) * 1.06
        self.assertEqual(low["profit_rub"], 189280)
        self.assertEqual(low["total_cost_rub"], 701720)
        ceiling = Decimal(str(low["max_bid_for_target"]["bid_rub"]))
        self.assertGreaterEqual(Decimal("891000")-(ceiling*Decimal("1.02")+50000)*Decimal("1.06"), 100000)
        self.assertLess(Decimal("891000")-((ceiling+Decimal(".01"))*Decimal("1.02")+50000)*Decimal("1.06"), 100000)
        self.assertIsNone(result["winning_probability"]["value"])

    def test_missing_not_zero_and_partial_scenarios(self):
        m = {"status":"estimated", "low_rub":100,"median_rub":100,"high_rub":100}
        self.assertEqual(finance.calculate(m, Costs(), 50)["decision"], "insufficient_data")
        c = costs(repair_high_rub=None)
        result = finance.calculate(m,c,50)
        self.assertEqual(result["scenarios"][0]["missing"], ["repair_high_rub"])
        self.assertEqual(result["scenarios"][1]["profit_rub"], 50)

    def test_invalid_money_and_repair_order(self):
        for value in ("NaN", "Infinity", "-1", "0.001", "100000000001"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Costs(transport_rub=value)
        with self.assertRaises(ValidationError):
            costs(repair_low_rub="2",repair_base_rub="1")

    def test_foreign_currency_not_silently_rubles(self):
        self.assertIsNone(service.bid_for({"price_currency":"USD","current_price":100},Inputs()))
        self.assertEqual(service.bid_for({"price_currency":"USD"},Inputs(bid_price_rub=100)),100)


class MarketTests(unittest.TestCase):
    def test_url_allowlist(self):
        valid = comparable().url
        self.assertEqual(market.canonical_listing_url(valid+"?token=remove#x"),valid.rstrip("/"))
        for value in ("http://auto.ru/cars/sale/123456/","https://auto.ru.evil.test/sale/123456/",
                      "https://127.0.0.1/sale/123456/","https://u:p@auto.ru/sale/123456/",
                      "https://auto.ru:8443/sale/123456/","https://auto.ru/cars/all/","file:///etc/passwd"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                market.canonical_listing_url(value)

    def test_currency_quote_not_monthly_payment(self):
        self.assertEqual(market.prices_in_quote("Цена 1\u202f200\u202f000 ₽"),[Decimal("1200000")])
        self.assertEqual(market.prices_in_quote("1,2 млн руб."),[Decimal("1200000")])
        for quote in ("от 800 000 ₽", "30 000 ₽ в месяц", "Цена 20000 USD", "кредит 20 000 руб"):
            self.assertEqual(market.prices_in_quote(quote),[])
        self.assertFalse(market.verified_price_quote("200 000 ₽", "Цена 1 200 000 ₽", Decimal(200000)))
        self.assertFalse(market.verified_price_quote("200 000 ₽", "Цена от 200 000 ₽", Decimal(200000)))
        self.assertFalse(market.verified_price_quote("20 000 ₽", "20 000 ₽ в месяц", Decimal(20000)))

    def test_filter_dedupe_stale_and_statistics(self):
        observations=[(comparable(i,str(p)),"user_entered") for i,p in enumerate([900000,1000000,1100000])]
        observations += [(comparable(4,"600000",year=2008),"user_entered"),
                         (comparable(5,"800000",observed_at=NOW-timedelta(days=91)),"user_entered"),
                         (comparable(0,"900000"),"user_entered")]
        result=market.estimate_market(VEHICLE,observations,NOW)
        self.assertEqual(result["sample_size"],3)
        self.assertEqual(result["median_rub"],1000000)
        self.assertEqual(result["mean_rub"],1000000)
        self.assertEqual(result["low_rub"],950000)
        self.assertEqual(len(result["excluded"]),3)
        insufficient=market.estimate_market(VEHICLE,observations[:2],NOW)
        self.assertIsNone(insufficient["mean_rub"])

    def test_outlier_critical_attributes_and_missing_dates(self):
        entries=[(comparable(i,str(p)),"fetched_page") for i,p in enumerate([900000,950000,1000000,1050000,9000000])]
        result=market.estimate_market(VEHICLE,entries,NOW)
        self.assertEqual(result["sample_size"],4)
        self.assertIn("выброс",result["excluded"][0]["reason"])
        strict=VEHICLE.model_copy(update={"transmission":"AT"})
        self.assertEqual(market.estimate_market(strict,entries,NOW)["sample_size"],0)
        naive=comparable(observed_at=NOW.replace(tzinfo=None))
        self.assertEqual(market.estimate_market(VEHICLE,[(naive,"user_entered")],NOW)["sample_size"],0)

    def test_source_price_must_be_literal(self):
        class Fake:
            def search(self,q): return [{"url":comparable().url,"snippet":"Цена 1 000 000 ₽"}]
            def fetch(self,url): return {"title":"Toyota Camry", "content":"Toyota Camry 2015. Полная цена 1 200 000 ₽"}
            def chat_json(self,*args):
                return MarketOutput(listings=[dict(source_id="web:1",kind="individual_listing",price_rub=1000000,
                  price_quote="1 000 000 ₽",identity_quote="Toyota Camry 2015",make="Toyota",model="Camry",year=2015,full_price=True)])
        accepted,sources,_=market.collect_market(Fake(),VEHICLE,NOW,1,1)
        self.assertEqual(accepted,[])
        self.assertIn("цитатой",sources[0]["reason"])

    def test_fetch_attempt_budget_includes_failures(self):
        class Fake:
            count=0
            def search(self,q):return [{"url":comparable(i).url} for i in range(10)]
            def fetch(self,url):
                self.count+=1
                raise ProviderError("blocked","Источник недоступен")
        fake=Fake()
        market.collect_market(fake,VEHICLE,NOW,2,3)
        self.assertEqual(fake.count,3)


class EvidenceTests(unittest.TestCase):
    def test_minimal_snapshot_and_redaction(self):
        lot={"id":1,"debtor":"Тестовый должник","debtor_inn":"123456789012", "contact_email":"private@example.test",
             "description":"Тестовый должник ИНН 123456789012 email private@example.test +7 (999) 123-45-67", "raw":"НЕ ОТПРАВЛЯТЬ",
             "condition_info":json.dumps({"condition_summary":"Есть царапины","raw_text":"СЕКРЕТ"})}
        value=evidence.snapshot(lot)
        text=json.dumps(value,ensure_ascii=False)
        for forbidden in ("Тестовый должник","123456789012","private@example.test","123-45-67","НЕ ОТПРАВЛЯТЬ","СЕКРЕТ"):
            self.assertNotIn(forbidden,text)
        self.assertIn("царапины",text)

    def test_photo_identity_paths_and_exif(self):
        folder=Path(TEMP.name)/"photos"
        folder.mkdir(exist_ok=True)
        path=folder/"test.jpg"
        image=Image.new("RGB",(2000,1000),"white")
        exif=Image.Exif(); exif[270]="PRIVATE METADATA"
        image.save(path,exif=exif)
        photos=[{"id":1,"status":"ok","local_path":"photos/test.jpg","source_site":"email_response"}]
        blocks,proof=evidence.prepare_photos(photos,[1],Path(TEMP.name),6)
        decoded=base64.b64decode(blocks[1]["image_url"]["url"].split(",")[1])
        with Image.open(io.BytesIO(decoded)) as output:
            self.assertLessEqual(output.width,1600)
            self.assertFalse(output.getexif())
        self.assertEqual(proof[0]["identity"],"user_confirmed")
        for selected,source in (([99],photos),([1],[dict(photos[0],local_path="../outside.jpg")])):
            with self.assertRaises(ValueError): evidence.prepare_photos(source,selected,Path(TEMP.name),6)
        with self.assertRaises(ValidationError):
            AnalysisRequest(request_id="test-req1",photo_ids=[1])

    def test_unknown_evidence_and_fabricated_quote_rejected(self):
        for finding in (dict(text="Повреждение",kind="inference",severity="high",evidence_id="missing"),
                        dict(text="Исправен",kind="reported_fact",severity="low",evidence_id="text",quote="исправен")):
            output=ConditionOutput(summary="Обзор",findings=[finding])
            with self.assertRaises(ValueError): evidence.validate_condition(output,{"text":"Неисправен"},[])


class FakeResponse:
    def __init__(self,data,status=200):self.body=json.dumps(data).encode();self.status_code=status
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,n):yield self.body


class ProviderTests(unittest.TestCase):
    def test_contract_truncation_and_safe_http_errors(self):
        class FakeSession:
            def __init__(self,reply):self.reply=reply
            def post(self,url,**kw):
                self.url,self.kw=url,kw
                return self.reply
        reply={"choices":[{"finish_reason":"stop","message":{"content":'{"summary":"Обзор"}'}}],"usage":{"prompt_tokens":20,"completion_tokens":5}}
        session=FakeSession(FakeResponse(reply))
        client=DeepSeekClient(AnalysisSettings(api_key="fake-only"),session)
        self.assertEqual(client.chat_json("JSON", "text",ConditionOutput).summary,"Обзор")
        self.assertEqual(client.usage["completion_tokens"],5)
        self.assertFalse(session.kw["allow_redirects"])
        reply["choices"][0]["finish_reason"]="length"
        session.reply=FakeResponse(reply)
        with self.assertRaises(ProviderError) as cm:client.chat_json("JSON","text",ConditionOutput)
        self.assertEqual(cm.exception.code,"incomplete")
        session.reply=FakeResponse({"error":"fake-only private body"},401)
        with self.assertRaises(ProviderError) as cm:client.search("query")
        self.assertNotIn("fake-only",str(cm.exception))


class APIAndStoreTests(unittest.TestCase):
    def setUp(self):
        with store.connection() as conn:
            conn.execute("DELETE FROM lot_analysis_requests")
            conn.execute("DELETE FROM lot_analysis_runs")
        db.execute("DELETE FROM photos")
        db.execute("DELETE FROM lots")
        self.lot_id,_=db.upsert_lot({"efrsb_id":"offline-lot", "title":"Toyota Camry 2015", "make":"Toyota", "model":"Camry", "year":2015,
                                   "description":"Кузов с царапинами", "price_amount":600000,"price_currency":"RUB"})
        self.client=TestClient(app)
        token=self.client.post("/api/admin/login",json={"password":"offline-test-only"}).json()["token"]
        self.headers={"X-Admin-Token":token}
        self.path=f"/api/lots/{self.lot_id}/analysis"
        self.settings=AnalysisSettings(api_key="fake-only")

    def request(self,**changes):
        data=dict(request_id="request-001",consent_to_external=True,search_market=False,vehicle=VEHICLE.model_dump(),
                  costs=costs().model_dump(mode="json"),manual_comparables=[comparable(i,str(1000000+i*10000)).model_dump(mode="json") for i in range(3)])
        data.update(changes)
        return AnalysisRequest(**data)

    def test_make_count_matches_filter_ui_contract(self):
        response = self.client.get("/api/makes")
        self.assertEqual(response.status_code, 200)
        item = next(row for row in response.json()["items"] if row["value"] == "Toyota")
        self.assertEqual(item["cnt"], 1)
        self.assertEqual(item["c"], 1)

    def test_auth_missing_key_and_offline_calculator(self):
        for method,path,body in (("get","/api/ai/status",None),("get",self.path,None),("post",self.path+"/calculate",{}),("post",self.path,self.request().model_dump(mode="json"))):
            self.assertEqual(self.client.request(method,path,json=body).status_code,401)
        self.assertFalse(self.client.get("/api/ai/status",headers=self.headers).json()["configured"])
        r=self.client.post(self.path,headers=self.headers,json=self.request().model_dump(mode="json"))
        self.assertEqual(r.status_code,503)
        body=self.request().model_dump(mode="json",exclude={"request_id","consent_to_external","search_market","question","photo_ids","confirm_photo_identity"})
        result=self.client.post(self.path+"/calculate",headers=self.headers,json=body)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["market"]["sample_size"],3)
        self.assertEqual(result.json()["economics"]["scenarios"][0]["status"],"calculated")
        self.assertEqual(self.client.get("/data/efrsb.db").status_code,404)

    def test_idempotency_cache_busy_and_daily_limit(self):
        lot=db.get_lot(self.lot_id)
        one=service.submit(lot,self.request(),self.settings,launch=False)
        two=service.submit(lot,self.request(),self.settings,launch=False)
        self.assertEqual(one["run"]["id"],two["run"]["id"])
        with self.assertRaises(store.RunConflict):service.submit(lot,self.request(question="Другой запрос"),self.settings,launch=False)
        with self.assertRaises(store.RunConflict):service.submit(lot,self.request(request_id="request-002",question="Другой запрос"),self.settings,launch=False)
        store.finish(one["run"]["id"],report={"test":True})
        cached=service.submit(lot,self.request(request_id="request-003"),self.settings,launch=False)
        self.assertEqual(cached["reuse"],"cache")
        with self.assertRaises(store.RunConflict) as cm:
            service.submit(lot,self.request(request_id="request-004",question="Новый"),AnalysisSettings(api_key="fake-only",daily_limit=1),launch=False)
        self.assertEqual(cm.exception.code,"daily_limit")

    def test_concurrent_submit_only_one_paid_job(self):
        def submit(i):
            return store.submit(self.lot_id,f"concurrent-{i}","same-input","same-snapshot",{},self.settings)
        with ThreadPoolExecutor(max_workers=4) as pool:replies=list(pool.map(submit,range(4)))
        self.assertEqual(sum(fresh for _,fresh,_ in replies),1)
        self.assertEqual(len({row["id"] for row,_,_ in replies}),1)

    def test_complete_mocked_job_and_staleness(self):
        lot=db.get_lot(self.lot_id);req=self.request()
        pending=service.submit(lot,req,self.settings,launch=False)
        class FakeClient:
            usage={"requests":1,"prompt_tokens":20,"completion_tokens":10}
            def __init__(self,settings):pass
            def chat_json(self,system,content,contract):
                return ConditionOutput(summary="По описанию есть царапины; осмотр необходим",findings=[dict(text="В описании указаны царапины",
                    kind="reported_fact",severity="low",evidence_id="lot:description",quote="Кузов с царапинами")],unknowns=["Состояние двигателя"],inspection_checks=["Осмотреть двигатель"])
        service.run_analysis(pending["run"]["id"],lot,req,self.settings,evidence.snapshot(lot),VEHICLE,[],[],FakeClient)
        result=self.client.get(self.path+"/runs/"+pending["run"]["id"],headers=self.headers)
        self.assertEqual(result.status_code,200,result.text)
        value=result.json();self.assertEqual(value["status"],"done");self.assertFalse(value["stale"])
        self.assertEqual(value["report"]["market"]["sample_size"],3)
        self.assertIsNone(value["report"]["economics"]["winning_probability"]["value"])
        db.execute("UPDATE lots SET description='Новый ответ управляющего' WHERE id=?",(self.lot_id,))
        self.assertTrue(self.client.get(self.path,headers=self.headers).json()["last_success"]["stale"])
        failed=service.submit(db.get_lot(self.lot_id),self.request(request_id="request-002"),self.settings,launch=False)
        store.finish(failed["run"]["id"],error=ProviderError("connection","Таймаут"))
        history=self.client.get(self.path,headers=self.headers).json()
        self.assertEqual(history["latest"]["status"],"error")
        self.assertEqual(history["last_success"]["id"],value["id"])

    def test_start_endpoint_launches_and_returns_pollable_run(self):
        def complete(run_id,*args):
            store.finish(run_id,report={"photo_evidence":[],"test":"endpoint-flow"})
        class ImmediateThread:
            def __init__(self,target,args,**kwargs):self.target,self.args=target,args
            def start(self):self.target(*self.args)
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"fake-only"}), \
             patch.object(service,"run_analysis",side_effect=complete), \
             patch.object(service.threading,"Thread",ImmediateThread):
            response=self.client.post(self.path,headers=self.headers,json=self.request().model_dump(mode="json"))
        self.assertEqual(response.status_code,202,response.text)
        run_id=response.json()["run"]["id"]
        result=self.client.get(self.path+"/runs/"+run_id,headers=self.headers).json()
        self.assertEqual(result["status"],"done")
        self.assertEqual(result["report"]["test"],"endpoint-flow")

    def test_recovery_and_cross_lot_report_access(self):
        run=service.submit(db.get_lot(self.lot_id),self.request(),self.settings,launch=False)["run"]
        store.init_store(recover=True)
        self.assertEqual(store.get_run(self.lot_id,run["id"])["error_code"],"interrupted")
        other,_=db.upsert_lot({"efrsb_id":"other","title":"Другой"})
        self.assertEqual(self.client.get(f"/api/lots/{other}/analysis/runs/{run['id']}",headers=self.headers).status_code,404)


if __name__ == "__main__":
    unittest.main()
