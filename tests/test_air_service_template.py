import io
from zipfile import ZipFile

import pytest
from PIL import Image
from lxml import etree as ET

from airmore.service.config import Settings
from airmore.service.examples import synthetic_case
from airmore.service.statistics import build_statistics
from airmore.service.composite import UnconfiguredComposite
from airmore.service.writer import WrittenAnalysis, fact_catalog, validate_composition
from airmore.service import strict_template as template


@pytest.mark.parametrize('count', [1, 5])
@pytest.mark.parametrize('report_type,title', [('DAY','日报'),('WEEK','周报'),('MONTH','月报')])
def test_source_components_and_data_bindings(count, report_type, title, tmp_path, monkeypatch):
    request,data,profiles=synthetic_case(count,missing=True)
    request.report_type=report_type
    stats=build_statistics(request,data,profiles)
    analyses={}
    for key,hangar in stats['hangars'].items():
        facts,allowed=fact_catalog(hangar)
        body={s:['，'.join('{'+i+'}' for i in ids[:2])+'。'] for s,ids in allowed.items()}
        analyses[key]=WrittenAnalysis(validate_composition(body,facts,allowed),body,facts,{'engine':'test'})
    buffer=io.BytesIO();Image.new('RGB',(100,70),'white').save(buffer,format='PNG')
    calls=[]
    monkeypatch.setattr(template,'pie',lambda record,runtime:buffer.getvalue())
    monkeypatch.setattr(template,'forecast',lambda end,runtime:buffer.getvalue())
    def trend(hangar,runtime):
        calls.append(hangar['name']);return buffer.getvalue()
    monkeypatch.setattr(template,'trend',trend)
    result=template.render_document(stats,analyses,UnconfiguredComposite().evaluate(stats),Settings(),tmp_path,{'font_family':'Songti SC'})
    assert result['image_count']==3*count+1
    assert calls==list(data.hangars.values())
    with ZipFile(template.TEMPLATE) as source, ZipFile(tmp_path/'report.docx') as rendered:
        for name in ['word/styles.xml','word/numbering.xml','word/theme/theme1.xml','word/settings.xml','word/fontTable.xml']:
            assert rendered.read(name)==source.read(name)
        a=template.xml(source.read('word/document.xml'));b=template.xml(rendered.read('word/document.xml'))
        ns=template.NS
        assert ET.tostring(a.find('w:body/w:sectPr',ns))==ET.tostring(b.find('w:body/w:sectPr',ns))
        tables=b.findall('w:body/w:tbl',ns)
        assert [len(t.findall('w:tr',ns)) for t in tables]==[2,count+1,count+1,2*count,count+1]
        for old,new in zip(a.findall('w:body/w:tbl',ns),tables):
            assert ET.tostring(old.find('w:tblGrid',ns))==ET.tostring(new.find('w:tblGrid',ns))
        texts=''.join(b.xpath('//w:t/text()',namespaces=ns))
        assert '{{' not in texts and '日AQI与数据覆盖' not in texts
        assert '空气质量研判'+title in texts and '预报数据待接入' in texts
        assert '全市排名125' not in texts and '46-76' not in texts
        ids=b.xpath('//wp:docPr/@id',namespaces=ns)
        assert len(ids)==len(set(ids))
        # No fake ranks or indices even when all sample statistics exist.
        for row in tables[1].findall('w:tr',ns)[1:]:
            cells=row.findall('w:tc',ns)
            assert ''.join(cells[-1].xpath('.//w:t/text()',namespaces=ns))=='/'
            assert ''.join(cells[-2].xpath('.//w:t/text()',namespaces=ns))=='/'
