import json
import pipeline
import store
from subtitle_sentences import sentence_rows, display_rows, text_width
from test_app import client, create_job


def row(text, start, end, **extra):
    return {'text':text,'start':start,'end':end,**extra}


def test_fragments_across_decoder_boundaries_become_one_sentence():
    result=sentence_rows([row('When I got home,',0,4),row('I called my friend',4.1,8),row('to tell her the news.',8.1,11),row('She was happy.',11.2,13)])
    assert [s['text'] for s in result] == ['When I got home,','I called my friend','to tell her the news.','She was happy.']
    assert (result[0]['start'],result[0]['end']) == (0,4)


def test_abbreviation_and_short_complete_sentence():
    result=sentence_rows([row('Dr.',0,.3),row('Smith is here.',.4,2),row('Yes!',2.1,2.5),row('Welcome.',2.6,3)])
    assert [s['text'] for s in result] == ['Dr. Smith is here.','Yes!','Welcome.']


def test_cjk_punctuation_and_silence_remain_boundaries():
    result=sentence_rows([row('今天',0,1),row('天气很好。',1.1,2),row('但是',3,4),row('下雨了',7,8)])
    assert [s['text'] for s in result] == ['今天天气很好。','但是','下雨了']


def test_very_long_unpunctuated_speech_still_has_readable_limits():
    result=sentence_rows([row('word',i,i+.8) for i in range(65)])
    assert all(s['end']-s['start']<=6 and text_width(s['text'])<=48 for s in result)
    assert ' '.join(s['text'] for s in result).split() == ['word']*65


def test_existing_paid_translations_group_without_rewriting_cache(client):
    item,folder=create_job()
    store.atomic_json(folder/'transcript.json', {'complete':True,'segments':[
        row('I called',1,2,id=0),row('my friend.',2.1,4,id=1)]})
    store.atomic_json(folder/'translated.json',{'lines':{'0':'我打电话','1':'给朋友。'}})
    before=(folder/'translated.json').read_bytes()
    result=pipeline.subtitle_data(item['id'])
    assert result == [row('I called my friend.',1,4,id=0,source_ids=[0,1],translation='我打电话给朋友。')]
    assert (folder/'translated.json').read_bytes() == before


def test_partial_translation_never_looks_complete_after_grouping():
    result=sentence_rows([row('I called',1,2,translation='我打电话'),row('my friend.',2.1,4,translation='')])
    assert len(result)==2 and result[1]['translation']==''


def test_legacy_long_rows_split_without_losing_text_or_paid_translation():
    source = ' '.join(['word']*39)
    translation = '这是已经付费翻译并保存的内容。'*7
    result = display_rows([row(source, 35, 48, id=4, translation=translation)])
    assert len(result)>1 and len({s['id'] for s in result})==len(result)
    assert all(text_width(s['text'])<=48 and text_width(s['translation'])<=48 for s in result)
    assert ' '.join(s['text'] for s in result).split() == source.split()
    assert ''.join(s['translation'] for s in result) == translation
    assert result[0]['start']==35 and result[-1]['end']==48
    assert all(a['end']==b['start'] for a,b in zip(result,result[1:]))


def test_embedded_newlines_are_normalized_per_language():
    result=display_rows([row('Hello\nworld.',0,2,id=0,translation='你好\n世界。')])
    assert result[0]['text']=='Hello world.'
    assert result[0]['translation']=='你好世界。'
