import datetime
import os
import threading
import time
import unittest
from unittest.mock import patch, Mock
from collector import event_curation as cur

class CurationTests(unittest.TestCase):
    def prefs(self, topics=None, keywords=None):
        return cur.preferences({'topics':topics or [],'keywords':keywords or []})

    def test_validation_and_dedup(self):
        self.assertEqual(self.prefs(['AI 윤리','AI 윤리'],[' AI 거버넌스 '])['keywords'],['AI 거버넌스'])
        for value in [None, [], {'topics':['unknown']},{'keywords':['x'*41]},{'topics':'AI 윤리'},{'keywords':[1]}]:
            with self.assertRaises(ValueError):cur.preferences(value)

    def test_pet_field_reaches_personalized_candidates(self):
        self.assertEqual(len(self.prefs(cur.TOPICS)['topics']), 8)
        rows = [{'title': title, 'start_date': '2099-01-01', 'url': str(i)}
                for i, title in enumerate(['궁디팡팡 캣페스타 SUWON', '냥냥펀치캣쇼', 'Pet Fair', '클라우드 포럼', 'Carpet design fair'])]
        self.assertEqual([r['url'] for r in cur.candidates(rows, self.prefs(['반려동물']))], ['0', '1', '2'])
        self.assertNotIn('반려동물', cur.categories({'title':'미술 전시', 'venue':'고양이센터'}))

    def test_relevance_and_expiry(self):
        rows=[{'title':'AI 윤리 거버넌스 포럼','start_date':'2099-01-01','end_date':'2099-01-01','url':'a'},
              {'title':'클라우드 보안 개발자 밋업','start_date':'2099-01-02','url':'b'},
              {'title':'미술 전시','start_date':'2099-01-01','url':'c'},
              {'title':'AI 윤리 포럼','end_date':'2000-01-01','url':'d'}]
        self.assertEqual([r['url'] for r in cur.candidates(rows,self.prefs(['AI 윤리']))],['a'])
        result=cur.candidates(rows,self.prefs(['AI 윤리'],['클라우드']))
        self.assertEqual(set(r['url'] for r in result),{'a','b'})
        self.assertEqual(len(cur.candidates(rows,self.prefs())),3)

    def test_model_output_cannot_create_new_events_or_duplicates(self):
        rows=[{'title':'Collected event','url':'known','curation_tags':['IT·기술']}]
        response=Mock(status_code=200);response.json.return_value={}
        picks={'picks':[{'id':100,'reason':'fake'},{'id':False,'reason':'fake'},{'id':0,'reason':'관련 분야'},{'id':0,'reason':'duplicate'}]}
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'test','EVENT_CURATION_MODEL':'test'}),patch('requests.post',return_value=response),patch.object(cur.analysis,'_text_from_response',return_value=''),patch.object(cur.analysis,'_extract_json',return_value=picks):
            result=cur.generate(rows,self.prefs())
        self.assertEqual(len(result),1);self.assertEqual(result[0]['url'],'known')

    def test_pending_coalesces_and_missing_ai_key_is_explicit_rules(self):
        with cur._lock:cur._jobs.clear()
        gate=threading.Event()
        def fetch():gate.wait(2);return [{'title':'Cloud 개발자','start_date':'2099-01-01','url':'known'}]
        with patch.dict(os.environ,{},clear=True),patch.object(cur.db,'list_events',side_effect=fetch) as dbread:
            prefs=self.prefs(['IT·기술'])
            self.assertEqual(cur.recommend(prefs)['status'],'pending')
            self.assertEqual(cur.recommend(prefs)['status'],'pending')
            gate.set()
            for _ in range(100):
                result=cur.recommend(prefs)
                if result['status']!='pending':break
                time.sleep(.01)
            self.assertEqual(result['mode'],'rules');self.assertEqual(result['items'][0]['url'],'known');self.assertEqual(dbread.call_count,1)

    def test_provider_credit_error_is_identified_without_exposing_message(self):
        response=Mock(status_code=400, text='credit balance too low')
        response.json.return_value={'error': {'message': 'Your credit balance is too low'}}
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'test','EVENT_CURATION_MODEL':'test'}),patch('requests.post',return_value=response):
            with self.assertRaises(RuntimeError) as error:
                cur.generate([],self.prefs())
        self.assertEqual(error.exception.curation_reason,'credit_balance')
        self.assertEqual(str(error.exception),'AI provider unavailable')
