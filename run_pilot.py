"""Run the locally frozen, synthetic development pilot. Standard library only."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import io
import itertools
import json
import platform
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from engine import FORMULAS, ERROR, PASS, FAIL, UNKNOWN, evaluate, score, zero_imputation, mean_aggregation, short_circuit
from oracle import oracle, cross_system_oracle

HERE = Path(__file__).resolve().parent


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def writer(path, fields):
    raw = path.open('wb')
    gz = gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0)
    stream = io.TextIOWrapper(gz, encoding='utf-8', newline='')
    out = csv.DictWriter(stream, fieldnames=fields)
    out.writeheader()
    return raw, stream, out


def vector(values):
    return '|'.join('U' if x is None else str(x) for x in values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    freeze = {
      'study_id': 'FAB-LOGIC-PILOT-20261024',
      'kind': 'synthetic_development_pilot_not_external_preregistration',
      'scope': 'Independent series candidate 3. Not Stage 1/Stage 2 production, calibration, certification, or DI estimation.',
      'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
      'formulas': [{'name':name,'expression':formula,'leaves':n} for name,formula,n in FORMULAS],
      'leaf_domain': [0,1,2,3,4,None,ERROR], 'thresholds':[1,2,3,4],
      'single_system_design':'Exhaustive on all 12 formula families and 7-state leaf assignments.',
      'cross_system_design':'Exactly two configurations; exhaustive 0..4 assignments for 2/3 leaves; 2000 uniform random ordered pairs with replacement per 4-leaf formula.',
      'seed':20261024, 'random_generator':'Python random.Random; ordered pairs sampled with replacement',
      'primary_checks':['strict_vs_independent_completion_oracle','known_value_cross_system_nonnegative_gap'],
      'comparators':['strict_four_state','zero_imputation','mean_aggregation','short_circuit','leafwise_best_across_systems'],
      'reporting':'Counts and rates conditional on constructed task distribution; no p values, real-world prevalence or clinical/human claims.',
      'excluded':'NOT, cycles, changing applicability, resource coupling, shared latent constraints, real gadgets and real technology evidence.',
      'code_sha256':{p.name:sha(p) for p in [HERE/'engine.py', HERE/'oracle.py', Path(__file__).resolve()]},
    }
    dump(out/'protocol_frozen.json', freeze)
    protocol_sha = sha(out/'protocol_frozen.json')

    comparisons = {'strict_four_state':evaluate,'zero_imputation':zero_imputation,'mean_aggregation':mean_aggregation,'short_circuit':short_circuit}
    counts = defaultdict(Counter)
    confusion = Counter()
    by_formula = defaultdict(Counter)
    cases = 0
    raw, stream, rows = writer(out/'single_system_cases.csv.gz', ['case_id','formula','values','threshold','gold','strict_four_state','zero_imputation','mean_aggregation','short_circuit'])
    for name, formula, n in FORMULAS:
        for values in itertools.product(freeze['leaf_domain'], repeat=n):
            for threshold in freeze['thresholds']:
                cases += 1
                gold = oracle(formula, values, threshold)
                row = {'case_id':cases,'formula':name,'values':vector(values),'threshold':threshold,'gold':gold}
                for method, fn in comparisons.items():
                    predicted = fn(formula,values,threshold)
                    row[method] = predicted
                    counts[method]['total'] += 1
                    counts[method]['correct'] += predicted == gold
                    counts[method]['gold_' + gold] += 1
                    confusion[method,name,gold,predicted] += 1
                    by_formula[method,name]['total'] += 1
                    by_formula[method,name]['mismatches'] += predicted != gold
                    if gold == FAIL and predicted == PASS:
                        counts[method]['false_pass'] += 1
                    if gold == UNKNOWN and predicted == PASS:
                        counts[method]['unsupported_pass'] += 1
                    if gold == PASS and predicted == FAIL:
                        counts[method]['false_fail'] += 1
                    if gold == UNKNOWN and predicted in (PASS,FAIL):
                        counts[method]['unknown_forced_decision'] += 1
                    if gold == ERROR and predicted != ERROR:
                        counts[method]['masked_error'] += 1
                    if all(type(x) is int for x in values):
                        counts[method]['fully_known_total'] += 1
                        counts[method]['fully_known_mismatches'] += predicted != gold
                rows.writerow(row)
    stream.close(); raw.close()

    rng = random.Random(freeze['seed'])
    cross = defaultdict(Counter)
    cross_cases = 0
    raw, stream, rows = writer(out/'cross_system_cases.csv.gz', ['case_id','formula','sampling','system_a','system_b','threshold','consistent_score','mixed_score','gold','mixed_prediction'])
    for name, formula, n in FORMULAS:
        if n <= 3:
            pairs = (tuple(v) for v in itertools.product(range(5), repeat=2*n))
            sampling = 'exhaustive'
        else:
            pairs = (tuple(rng.randrange(5) for _ in range(2*n)) for _ in range(2000))
            sampling = 'random_with_replacement'
        for pair in pairs:
            a,b = pair[:n],pair[n:]
            consistent_score = max(score(formula,a),score(formula,b))
            mixed_score = score(formula,[max(x,y) for x,y in zip(a,b)])
            assert mixed_score >= consistent_score
            for threshold in freeze['thresholds']:
                cross_cases += 1
                gold = cross_system_oracle(formula,[a,b],threshold)
                direct = PASS if consistent_score >= threshold else FAIL
                assert direct == gold
                predicted = PASS if mixed_score >= threshold else FAIL
                key=(name,sampling)
                cross[key]['total'] += 1
                cross[key]['false_pass'] += gold == FAIL and predicted == PASS
                cross[key]['false_fail'] += gold == PASS and predicted == FAIL
                cross[key]['oracle_fail'] += gold == FAIL
                cross[key]['score_inflated'] += mixed_score > consistent_score
                cross[key]['total_score_gap'] += mixed_score-consistent_score
                rows.writerow({'case_id':cross_cases,'formula':name,'sampling':sampling,'system_a':vector(a),'system_b':vector(b),'threshold':threshold,'consistent_score':consistent_score,'mixed_score':mixed_score,'gold':gold,'mixed_prediction':predicted})
    stream.close(); raw.close()

    examples = [
      {'name':'mean_false_pass','formula':('AND',0,1),'values':[4,2],'threshold':3},
      {'name':'mean_false_fail','formula':('OR',0,1),'values':[4,0],'threshold':3},
      {'name':'unknown_is_not_zero','formula':('AND',0,1),'values':[4,None],'threshold':3},
      {'name':'error_cannot_hide_behind_success','formula':('OR',0,1),'values':[4,ERROR],'threshold':3},
    ]
    for example in examples:
        example['gold'] = oracle(example['formula'],example['values'],example['threshold'])
        example['predictions'] = {k:fn(example['formula'],example['values'],example['threshold']) for k,fn in comparisons.items()}
    examples.append({'name':'different_systems_do_not_make_one_integrated_system','formula':['AND',0,1],'systems':[[4,0],[0,4]],'threshold':3,'gold':'FAIL','mixed_prediction':'PASS','consistent_score':0,'mixed_score':4})
    dump(out/'counterexamples.json',examples)
    with (out/'confusion.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['method','formula','gold','prediction','count'])
        for key,count in sorted(confusion.items()):w.writerow([*key,count])
    with (out/'cross_system_summary.csv').open('w',encoding='utf-8',newline='') as f:
        fields=['formula','sampling','total','false_pass','false_fail','oracle_fail','score_inflated','total_score_gap']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for (name,sampling),ct in cross.items(): w.writerow({'formula':name,'sampling':sampling,**dict(ct)})
    totals=Counter()
    for ct in cross.values():totals.update(ct)
    summary={
      'study_id':freeze['study_id'],'protocol_sha256':protocol_sha,
      'single_system_cases':cases,'single_system_metrics':{k:dict(v) for k,v in counts.items()},
      'cross_system_threshold_cases':cross_cases,'cross_system_metrics':dict(totals),
      'cross_system_by_formula':[{'formula':name,'sampling':sampling,**dict(v)} for (name,sampling),v in cross.items()],
      'exhaustive_cross_threshold_cases':sum(v['total'] for (n,s),v in cross.items() if s=='exhaustive'),
      'random_cross_threshold_cases':sum(v['total'] for (n,s),v in cross.items() if s=='random_with_replacement'),
      'environment':{'python':sys.version,'platform':platform.platform()},
      'completed_at_utc':datetime.now(timezone.utc).isoformat(),
      'interpretation':'Development verification only; no real technology performance, clinical, human or novelty claims.',
    }
    dump(out/'summary.json',summary)
    assert counts['strict_four_state']['correct'] == cases
    assert counts['mean_aggregation']['fully_known_mismatches'] > 0
    assert totals['false_pass'] > 0 and totals['false_fail'] == 0
    print(json.dumps({'output':str(out),'single_system_cases':cases,'strict_mismatches':cases-counts['strict_four_state']['correct'],'cross_system_threshold_cases':cross_cases,'cross_false_pass':totals['false_pass']},ensure_ascii=False))


if __name__ == '__main__':
    main()
