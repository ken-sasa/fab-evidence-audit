"""New development extension with local pre-run commitments, not preregistration."""
import argparse, csv, gzip, hashlib, io, itertools, json, random, sys
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
import engine_context as E
from oracle_context import oracle,best_witness_exists

HERE=Path(__file__).resolve().parent
FAMILIES=['CLEAN_PASS','CLEAN_FAIL','UNKNOWN_GAP','CROSS_CONFIG','CONTEXT_FALSE','TARGET_CONFLICT','TARGET_ERROR','OFFCONTEXT_ERROR','VALID_OR_ROUTE','DUPLICATE_SUPPORT']
EXPECTED={'CLEAN_PASS':'PASS','CLEAN_FAIL':'FAIL','UNKNOWN_GAP':'UNKNOWN','CROSS_CONFIG':'FAIL','CONTEXT_FALSE':'FAIL','TARGET_CONFLICT':'ERROR','TARGET_ERROR':'ERROR','OFFCONTEXT_ERROR':'PASS','VALID_OR_ROUTE':'PASS','DUPLICATE_SUPPORT':'PASS'}
METHODS={'witness_evaluator':lambda t:E.evaluate(t)['state'],'relational_reference':E.relational_reference,'config_only_conservative':E.config_only_conservative,'context_only_conservative':E.context_only_conservative,'optimistic_pool':E.optimistic_pool,'missing_as_fail':E.missing_as_fail}

def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sig(f):
    if type(f) is int:return 'L'
    kids=[]
    for c in f[1:]:
        if type(c) is tuple and c[0]==f[0]:kids.extend(flat_children(c))
        else:kids.append(sig(c))
    return f[0]+'('+','.join(sorted(kids))+')'
def flat_children(f):
    vals=[]
    for c in f[1:]:
        if type(c) is tuple and c[0]==f[0]:vals.extend(flat_children(c))
        else:vals.append(sig(c))
    return vals
def tree(indices,rng):
    if len(indices)==1:return indices[0]
    cut=rng.randrange(1,len(indices))
    return (rng.choice(['AND','OR']),tree(indices[:cut],rng),tree(indices[cut:],rng))
def catalogue():
    out=[('NEW4_AND',('AND',0,1,('OR',2,3)),4),('NEW4_OR',('OR',0,1,('AND',2,3)),4),('REPEAT4',('AND',('OR',0,1),('OR',1,2),('OR',2,3),('OR',0,3)),4)]
    rng=random.Random(2026102402)
    seen={sig(f) for _,f,_ in out}
    for n,count in [(5,6),(6,7)]:
        k=0
        while k<count:
            f=tree(list(range(n)),rng)
            if sig(f) in seen or 'AND' not in repr(f) or 'OR' not in repr(f):continue
            # Require both a partially false passing route and a cross-config gap.
            vectors=list(itertools.product([False,True],repeat=n))
            good=[v for v in vectors if E.truth(f,dict(enumerate(v)))]
            bad=[v for v in vectors if not E.truth(f,dict(enumerate(v)))]
            if not any(not all(v) for v in good):continue
            if not any(E.truth(f,{i:a[i] or b[i] for i in range(n)}) for a in bad for b in bad):continue
            seen.add(sig(f));k+=1;out.append((f'NEW{n}_{k}',f,n))
    return out
def task_for(name,f,n,family,rep,rng):
    vectors=list(itertools.product([False,True],repeat=n))
    good=[v for v in vectors if E.truth(f,dict(enumerate(v)))]
    partial=[v for v in good if not all(v)]
    bad=[v for v in vectors if not E.truth(f,dict(enumerate(v)))]
    configs=[f'S{rep%7}_{i}' for i in range(3)]
    target=f'mode={rng.choice(["A","B","C"])};temperature={rng.choice(["room","cold"])};resource={rng.choice(["P","Q"])}'
    alt=target+';different_test_setting=yes'
    arrays=[list(rng.choice(bad)) for _ in configs]
    if family in ['CLEAN_PASS','OFFCONTEXT_ERROR','VALID_OR_ROUTE','DUPLICATE_SUPPORT','TARGET_CONFLICT']:
        arrays[0]=list(rng.choice(partial if family=='VALID_OR_ROUTE' else good))
    if family=='UNKNOWN_GAP':arrays[0]=[None]*n
    if family=='CLEAN_FAIL':arrays=[arrays[0][:] for _ in configs]
    if family=='CONTEXT_FALSE':arrays=[[False]*n for _ in configs]
    if family=='CROSS_CONFIG':
        pairs=[(a,b) for a in bad for b in bad if E.truth(f,{i:a[i] or b[i] for i in range(n)})]
        a,b=rng.choice(pairs);arrays[0]=list(a);arrays[1]=list(b)
    task={'task_id':f'{name}:{family}:{rep:03d}','formula_name':name,'family':family,'replicate':rep,'formula':f,'requirements':n,'configs':configs,'target_context':target,'records':[]}
    rows=task['records']
    def add(c,context,i,result):
        rows.append({'record_id':f'R{len(rows):04d}','config_id':c,'context':context,'requirement':i,'result':result})
    for c,values in zip(configs,arrays):
        for i,v in enumerate(values):
            if v is not None:add(c,target,i,'PASS' if v else 'FAIL')
    # Clean controls keep target/off-context records consistent. Only the two
    # context-contamination families deliberately insert incompatible support.
    for c,values in zip(configs,arrays):
        other=[True]*n if family in ['UNKNOWN_GAP','CONTEXT_FALSE'] else values
        for i,v in enumerate(other):
            if v is not None:add(c,alt,i,'PASS' if v else 'FAIL')
    if family=='TARGET_CONFLICT':
        i=next(i for i,v in enumerate(arrays[0]) if v)
        add(configs[0],target,i,'FAIL')
    if family=='TARGET_ERROR':add(configs[0],target,rng.randrange(n),'ERROR')
    if family=='OFFCONTEXT_ERROR':add(configs[rng.randrange(3)],alt,rng.randrange(n),'ERROR')
    if family=='DUPLICATE_SUPPORT':
        for r in list(rows):
            if r['config_id']==configs[0] and r['context']==target and r['result']=='PASS':add(r['config_id'],r['context'],r['requirement'],r['result'])
    rng.shuffle(rows)
    return task
def witness_check(task,result):
    w=result['witness']
    if result['state']!='PASS':return w is None
    if w is None or w['config_id'] not in task['configs'] or w['context']!=task['target_context']:return False
    byid={r['record_id']:r for r in task['records']}
    records=[byid.get(i) for i in w['record_ids']]
    if not records or any(r is None or r['config_id']!=w['config_id'] or r['context']!=w['context'] or r['result']!='PASS' for r in records):return False
    have={r['requirement'] for r in records}
    return E.truth(task['formula'],{i:i in have for i in E.leaves(task['formula'])})
def metamorph(task,result,rng):
    output=[]
    shuffled=dict(task);shuffled['records']=list(task['records']);shuffled['configs']=list(task['configs'])
    rng.shuffle(shuffled['records']);rng.shuffle(shuffled['configs'])
    output.append(('row_config_order',E.evaluate(shuffled)==result))
    off=dict(task);off['records']=task['records']+[{'record_id':'META_OFF','config_id':task['configs'][0],'context':task['target_context']+';irrelevant=yes','requirement':0,'result':'ERROR'}]
    output.append(('offcontext_invariance',E.evaluate(off)==result))
    dup=dict(task);r=dict(task['records'][0]);r['record_id']='ZZZ_META_DUP';dup['records']=task['records']+[r]
    output.append(('duplicate_invariance',E.evaluate(dup)==result))
    # Renaming evidence identifiers preserves state and proof validity, not its tie-break choice.
    renamed=dict(task);renamed['records']=[dict(r,record_id='NEW_'+r['record_id']) for r in task['records']]
    v=E.evaluate(renamed)
    output.append(('record_id_renaming',v['state']==result['state'] and witness_check(renamed,v)))
    return output
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    formulas=catalogue()
    sys.path.insert(0,str(HERE.parent));from engine import FORMULAS as OLD
    old={sig(f) for _,f,_ in OLD}
    assert not old & {sig(f) for _,f,_ in formulas}
    protocol={'study_id':'FAB-CONTEXT-DEV-20261024','version':'0.2','kind':'new_development_extension_after_v01_not_external_preregistration_or_confirmatory_holdout','frozen_at_utc':datetime.now(timezone.utc).isoformat(),'generator_seed':2026102403,'metamorphic_seed':2026102404,'replicates_per_formula_family':32,'formula_catalogue':[{'name':name,'formula':f,'requirements':n,'normalized_shape':sig(f)} for name,f,n in formulas],'families':FAMILIES,'query_semantics':'Some single listed configuration satisfies a monotone formula in the one explicitly required common target context. Different requirement-specific contexts are outside this model.','error_policy':'Conflicting or explicit ERROR evidence in any referenced target-context cell dominates globally; unrelated-context errors are ignored.','comparison_methods':list(METHODS),'primary_checks':['agreement_with_independent_completion_oracle','agreement_with_conventional_relational_reference','valid_single_configuration_context_witness_for_each_PASS'],'metamorphic_checks':['row_config_order','offcontext_invariance','duplicate_invariance','record_id_renaming'],'sample_scope':'Constructed family-balanced design, not a random population sample. Variants of one task are property tests, not independent observations.','code_sha256':{p.name:sha(p) for p in [HERE/'engine_context.py',HERE/'oracle_context.py',Path(__file__).resolve()]},'old_pilot_protocol_sha256':sha(HERE.parent/'results/pilot_v0_1/protocol_frozen.json'),'scope_exclusions':['human data','real technology evidence','main FAB data','E/A ratings','missing key metadata','nonmonotone constraints','causal performance effects']}
    protocol['version']='0.2-refined'
    protocol['kind']='exploratory_design_refinement_after_inspecting_initial_v02_results_not_confirmatory'
    protocol['development_history']={'initial_v02_results_accessed':True,'initial_protocol_sha256':sha(HERE.parent/'results/context_v0_2/protocol_frozen.json'),'reason':'Initial generator placed off-context PASS evidence in every family, making optimistic pooling constant PASS. Refine clean controls to retain consistent off-context evidence; contaminate context only in the designated families. Initial sources and data are retained.','scope':'Dataset-design refinement, not an outcome-blind held-out confirmation. Evaluation semantics and engines unchanged.'}
    dump(out/'protocol_frozen.json',protocol)
    rng=random.Random(protocol['generator_seed']);meta_rng=random.Random(protocol['metamorphic_seed'])
    totals=defaultdict(Counter);byfamily=defaultdict(Counter);confusion=Counter();props=defaultdict(Counter);goldcounts=Counter()
    tasks_raw=(out/'tasks.jsonl.gz').open('wb');tasks_gz=gzip.GzipFile(filename='',mode='wb',fileobj=tasks_raw,mtime=0);tasks=io.TextIOWrapper(tasks_gz,encoding='utf-8')
    pred_raw=(out/'predictions.csv.gz').open('wb');pred_gz=gzip.GzipFile(filename='',mode='wb',fileobj=pred_raw,mtime=0);pred=io.TextIOWrapper(pred_gz,encoding='utf-8',newline='');w=csv.DictWriter(pred,fieldnames=['task_id','formula','family','gold',*METHODS,'witness_valid','oracle_witness_exists']);w.writeheader()
    witness_out=(out/'witnesses.jsonl').open('w',encoding='utf-8')
    examples={};count=0
    for name,f,n in formulas:
        for family in FAMILIES:
            for rep in range(32):
                task=task_for(name,f,n,family,rep,rng);count+=1
                gold=oracle(task);assert gold==EXPECTED[family],(task['task_id'],gold)
                result=E.evaluate(task);valid=witness_check(task,result);exists=best_witness_exists(task)
                assert valid and (gold!='PASS' or exists)
                goldcounts[gold]+=1
                row={'task_id':task['task_id'],'formula':name,'family':family,'gold':gold,'witness_valid':int(valid),'oracle_witness_exists':int(exists)}
                for method,fn in METHODS.items():
                    answer=fn(task);row[method]=answer
                    for target in [totals[method],byfamily[method,family]]:
                        target['total']+=1;target['correct']+=answer==gold;target['false_pass']+=gold=='FAIL' and answer=='PASS';target['unsupported_pass']+=gold=='UNKNOWN' and answer=='PASS';target['unknown_forced_fail']+=gold=='UNKNOWN' and answer=='FAIL';target['masked_error']+=gold=='ERROR' and answer!='ERROR';target['excess_error']+=gold!='ERROR' and answer=='ERROR';target['missed_pass']+=gold=='PASS' and answer!='PASS'
                    confusion[method,family,gold,answer]+=1
                for prop,ok in metamorph(task,result,meta_rng):props[prop]['total']+=1;props[prop]['passed']+=ok
                props['witness_validity']['total']+=1;props['witness_validity']['passed']+=valid
                if gold=='PASS':props['oracle_positive_witness']['total']+=1;props['oracle_positive_witness']['passed']+=exists
                tasks.write(json.dumps(task,ensure_ascii=False,separators=(',',':'))+'\n');w.writerow(row)
                if result['witness'] is not None:witness_out.write(json.dumps({'task_id':task['task_id'],**result['witness']},sort_keys=True)+'\n')
                examples.setdefault(family,{'task':task,'gold':gold,'predictions':{m:row[m] for m in METHODS},'witness':result['witness']})
    tasks.close();tasks_raw.close();pred.close();pred_raw.close();witness_out.close()
    with (out/'confusion.csv').open('w',encoding='utf-8',newline='') as f:
        wr=csv.writer(f);wr.writerow(['method','family','gold','prediction','count'])
        for key,value in sorted(confusion.items()):wr.writerow([*key,value])
    dump(out/'examples.json',examples)
    summary={'study_id':protocol['study_id'],'task_count':count,'formula_count':len(formulas),'family_count':len(FAMILIES),'configs_per_task':3,'gold_counts':dict(goldcounts),'metrics':{k:dict(v) for k,v in totals.items()},'family_metrics':[{'method':m,'family':f,**dict(v)} for (m,f),v in byfamily.items()],'property_checks':{k:dict(v) for k,v in props.items()},'protocol_sha256':sha(out/'protocol_frozen.json'),'completed_at_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version,'label':'Exploratory development extension on constructed tasks. No real-world prevalence or superiority over correct relational filtering is claimed.'}
    dump(out/'summary.json',summary)
    assert totals['witness_evaluator']['correct']==count
    assert totals['relational_reference']['correct']==count
    assert all(v['total']==v['passed'] for v in props.values())
    print(json.dumps({'output':str(out),'tasks':count,'gold_counts':dict(goldcounts),'metrics':summary['metrics'],'properties':summary['property_checks']},ensure_ascii=False))
if __name__=='__main__':main()
