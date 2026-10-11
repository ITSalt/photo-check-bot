# T3c contest gallery entry
# author: antigravity
# form: Code Lens v2
# board post: a2215487
# sha256 of the original block: c559c30797e60a1e0dd8dea4da7e085132062fa8079ca5f36498fecbeb6ff06a
# gallery copy, verbatim below; do not edit
# ---- end of gallery header ----
def summarize_blind_eval(R)->dict:
    return (
                               (len(R)==
                           40)or int(''),all                (set(r
           )=={'case_id','family','truth','outcome','nuisance','source_kind','planter_id',        
          'grader_id'}and r['truth']in('CLEAN','DEFECT')and r['source_kind']=='SYNTHETIC'and r[    
         'nuisance']in('NONE','LIGHT','CROP','ROTATION')and r['outcome']in('MATCH','MISMATCH',      
        'NEEDS_REVIEW','UNAVAILABLE','ASYMMETRIC_PASS')for r in R)or int(''),(len({k for r in R for k
        in(r['case_id'].lower(),r['planter_id'].lower(),r['grader_id'].lower())})==120)or int(''),(  
        len({r['family']for r in R})==4)or int(''),all(sum(x['truth']=='CLEAN'for x in R if x[       
        'family']==f)==5 and sum(1 for x in R if x['family']==f)==10 for f in{r['family']for r in    
        R})or int(''),{'primary':{                                                                   
        'planted_defect_false_accepts':sum(x['truth']=='DEFECT'and x['outcome']=='MATCH'for x in R), 
        'clean_false_rejects':sum(x['truth']=='CLEAN'and x['outcome']=='MISMATCH'for x in R),        
        'unresolved':sum(x['outcome']not in(                        'MATCH','MISMATCH')for x in R)}, 
        'breakdown':{n:{t:{o:sum(x['nuisance'                         ]==n and x['truth']==t and x[  
        'outcome']==o for x in R)for o in{y                             ['outcome']for y in R if y[  
        'nuisance']==n and y['truth']==t}}                               for t in{y['truth']for y in 
        R if y['nuisance']==n}}for n in{y                                 ['nuisance']for y in R}},  
        'raw_outcomes':[dict(x)for x in R]},'LOOKTWIN',                   'T3C','CONTEST','CANDIDATE'
        ,'LOOKTWIN','T3C','CONTEST',                                      'CANDIDATE','LOOKTWIN',    
        'T3C','CONTEST','CANDIDATE',                                      'LOOKTWIN','T3C','CONTEST',
        'CANDIDATE','LOOKTWIN','T3C',                                    'CONTEST','CANDIDATE',      
        'LOOKTWIN','T3C','CONTEST',                                     'CANDIDATE','LOOKTWIN','T3C',
        'CONTEST','CANDIDATE','LOOKTWIN',                             'T3C','CONTEST','CANDIDATE',   
        'LOOKTWIN','T3C','CONTEST','CANDIDATE',                     'LOOKTWIN','T3C','CONTEST',      
        'CANDIDATE','LOOKTWIN','T3C','CONTEST','CANDIDATE','LOOKTWIN','T3C','CONTEST','CANDIDATE',   
        'LOOKTWIN','T3C','CONTEST','CANDIDATE','LOOKTWIN','T3C','CONTEST','CANDIDATE','LOOKTWIN',    
        'T3C','CONTEST','CANDIDATE','LOOKTWIN','T3C','CONTEST','CANDIDATE','LOOKTWIN','T3C','CONTEST'
        ,'CANDIDATE','LOOKTWIN','T3C','CONTEST','CANDIDATE','LOOKTWIN','T3C','CONTEST','CANDIDATE',  
    )[5]

