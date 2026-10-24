"""Synthetic fixed-context query evaluator and intentionally weaker contrasts.

The query explicitly requires one configuration and one common target context.
This is not a universal rule that every engineering requirement shares context.
"""
from itertools import product


def leaves(formula):
    if type(formula) is int:
        return {formula}
    if not isinstance(formula,tuple) or len(formula)<3 or formula[0] not in ('AND','OR'):
        raise ValueError('invalid formula')
    return set().union(*(leaves(c) for c in formula[1:]))


def truth(formula, values):
    if type(formula) is int:
        return values[formula]
    children=[truth(c,values) for c in formula[1:]]
    return all(children) if formula[0]=='AND' else any(children)


def validate(task):
    n=task['requirements']
    if type(n) is not int or n<1 or not leaves(task['formula']) <= set(range(n)):
        raise ValueError('invalid requirement domain')
    cs=task['configs']
    if not cs or len(set(cs))!=len(cs) or any(not isinstance(c,str) or not c for c in cs):
        raise ValueError('invalid configurations')
    if not isinstance(task['target_context'],str) or not task['target_context']:
        raise ValueError('invalid target context')
    ids=set()
    for r in task['records']:
        if r['config_id'] not in cs or not isinstance(r['context'],str) or not r['context']:
            raise ValueError('invalid record keys')
        if type(r['requirement']) is not int or not 0<=r['requirement']<n or r['result'] not in ('PASS','FAIL','ERROR'):
            raise ValueError('invalid record result')
        if not isinstance(r['record_id'],str) or not r['record_id'] or r['record_id'] in ids:
            raise ValueError('invalid or duplicate record ID')
        ids.add(r['record_id'])


def resolve(rows):
    values={r['result'] for r in rows}
    if 'ERROR' in values or {'PASS','FAIL'}<=values:return 'ERROR'
    if 'PASS' in values:return True
    if 'FAIL' in values:return False
    return None


def bounds_state(formula, cells):
    if 'ERROR' in cells.values():return 'ERROR'
    lo={i:(v if type(v) is bool else False) for i,v in cells.items()}
    hi={i:(v if type(v) is bool else True) for i,v in cells.items()}
    if truth(formula,lo):return 'PASS'
    return 'UNKNOWN' if truth(formula,hi) else 'FAIL'


def outer(states):
    if 'ERROR' in states:return 'ERROR'
    if 'PASS' in states:return 'PASS'
    return 'FAIL' if all(s=='FAIL' for s in states) else 'UNKNOWN'


def support_sets(formula, positive):
    if type(formula) is int:
        return {frozenset([formula])} if formula in positive else set()
    groups=[support_sets(c,positive) for c in formula[1:]]
    if formula[0]=='OR':
        sets=set().union(*groups)
    else:
        sets={frozenset().union(*parts) for parts in product(*groups)} if all(groups) else set()
    return {s for s in sets if not any(t<s for t in sets)}


def evaluate(task):
    validate(task)
    active=leaves(task['formula'])
    prepared=[]
    for config in sorted(task['configs']):
        selected=[r for r in task['records'] if r['config_id']==config and r['context']==task['target_context'] and r['requirement'] in active]
        cells={i:resolve([r for r in selected if r['requirement']==i]) for i in active}
        prepared.append((config,selected,cells,bounds_state(task['formula'],cells)))
    state=outer([row[3] for row in prepared])
    witness=None
    if state=='PASS':
        choices=[]
        for config,rows,cells,status in prepared:
            if status!='PASS':continue
            for reqs in support_sets(task['formula'],{i for i,v in cells.items() if v is True}):
                ids=tuple(sorted(min(r['record_id'] for r in rows if r['requirement']==i and r['result']=='PASS') for i in reqs))
                choices.append((len(ids),ids,config))
        _,ids,config=min(choices)
        witness={'config_id':config,'context':task['target_context'],'record_ids':list(ids)}
    return {'state':state,'witness':witness}


def relational_reference(task):
    """Conventional filtered relation plus a separate four-state tree reduction."""
    validate(task)
    active=leaves(task['formula'])
    groups={}
    for r in task['records']:
        if r['context']==task['target_context'] and r['requirement'] in active:
            groups.setdefault((r['config_id'],r['requirement']),set()).add(r['result'])
    def reduce_node(node,cells):
        if type(node) is int:return cells[node]
        child=[reduce_node(c,cells) for c in node[1:]]
        if 'ERROR' in child:return 'ERROR'
        if node[0]=='AND':return 'FAIL' if 'FAIL' in child else ('PASS' if all(s=='PASS' for s in child) else 'UNKNOWN')
        return 'PASS' if 'PASS' in child else ('FAIL' if all(s=='FAIL' for s in child) else 'UNKNOWN')
    states=[]
    for config in task['configs']:
        cells={}
        for i in active:
            values=groups.get((config,i),set())
            cells[i]='ERROR' if ('ERROR' in values or {'PASS','FAIL'}<=values) else ('PASS' if 'PASS' in values else ('FAIL' if 'FAIL' in values else 'UNKNOWN'))
        states.append(reduce_node(task['formula'],cells))
    return outer(states)


def config_only_conservative(task):
    active=leaves(task['formula'])
    states=[]
    for c in task['configs']:
        cells={i:resolve([r for r in task['records'] if r['config_id']==c and r['requirement']==i]) for i in active}
        states.append(bounds_state(task['formula'],cells))
    return outer(states)


def context_only_conservative(task):
    active=leaves(task['formula'])
    cells={i:resolve([r for r in task['records'] if r['context']==task['target_context'] and r['requirement']==i]) for i in active}
    return bounds_state(task['formula'],cells)


def optimistic_pool(task):
    values={i:any(r['requirement']==i and r['result']=='PASS' for r in task['records']) for i in leaves(task['formula'])}
    return 'PASS' if truth(task['formula'],values) else 'FAIL'


def missing_as_fail(task):
    active=leaves(task['formula'])
    states=[]
    for c in task['configs']:
        cells={i:resolve([r for r in task['records'] if r['config_id']==c and r['context']==task['target_context'] and r['requirement']==i]) for i in active}
        if 'ERROR' in cells.values():states.append('ERROR')
        else:states.append('PASS' if truth(task['formula'],{i:v is True for i,v in cells.items()}) else 'FAIL')
    return outer(states)
