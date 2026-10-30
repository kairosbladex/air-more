"""Create stable product-style reports atomically from frozen data and GPU advice."""
import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from airmore.regional.product_periods import load_observations, build_period
from airmore.regional.product_render import render, convert_pdf
from airmore.regional.render import preflight


def generate(sources, advice_dir, output, as_of, kinds, soffice):
    sources, advice_dir, output = Path(sources), Path(advice_dir), Path(output)
    runtime = preflight(soffice)
    rows=load_observations(sources)
    output.mkdir(parents=True,exist_ok=True)
    results=[]
    for kind in kinds:
        period=build_period(rows,kind,as_of)
        stem='郑州市空气质量研判'+period['name']+'_'+period['start'].replace('-','')+'_'+period['end'].replace('-','')
        target=output/stem
        if target.exists():raise FileExistsError('Report exists; choose a new output directory: '+str(target))
        advice=json.loads((advice_dir/(kind+'-advice.json')).read_text())
        lock=output/('.'+stem+'.lock')
        fd=os.open(lock,os.O_CREAT | os.O_EXCL | os.O_WRONLY,0o600)
        os.close(fd)
        staging=None
        try:
            if target.exists():raise FileExistsError(str(target))
            staging=Path(tempfile.mkdtemp(prefix='.product-report-',dir=output))
            docx=staging/(stem+'.docx')
            evidence=render(period,advice,sources,docx,runtime=runtime)
            pdf=convert_pdf(docx,runtime['soffice'])
            evidence['files']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (docx,pdf)}
            evidence['status']='complete_with_gaps' if period['current']['missing'] else 'complete'
            (staging/'verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
            if target.exists():raise FileExistsError(str(target))
            staging.rename(target)
            results.append({'kind':kind,'status':evidence['status'],'docx':str(target/docx.name),'pdf':str(target/pdf.name)})
            print(json.dumps(results[-1],ensure_ascii=False),flush=True)
        finally:
            if staging is not None and staging.exists():shutil.rmtree(staging)
            lock.unlink(missing_ok=True)
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,required=True)
    p.add_argument('--advice-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--as-of',type=date.fromisoformat,required=True)
    p.add_argument('--types',nargs='+',choices=['DAY','WEEK','MONTH'],default=['WEEK','MONTH'])
    p.add_argument('--soffice',type=Path,required=True)
    a=p.parse_args();generate(a.sources,a.advice_dir,a.output,a.as_of,a.types,a.soffice)


if __name__=='__main__':main()
