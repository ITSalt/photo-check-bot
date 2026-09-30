"""Public T3 contract entry point; bot implementation ships inside app/.

T3b call_provider and T3c summarize_blind_eval: "The Code Lens" by antigravity
(getpostingboard.dev contest 8482136a, post fe64c0cd; T3c form entry v2 a2215487).
"""
from app.photo_compare import ProviderUnavailable, run_bot_pair


def call_provider(request, transport, environ, logger):
    key = environ.get("VISION_API_KEY")
    if not key:
        raise ProviderUnavailable("MISSING_KEY")
    
    headers = {"Authorization": f"Bearer {key}"}
    
    res = None
    err_type = None
    
    try:
        res = transport(request, headers, 60)
    except Exception as e:
        if isinstance(e, TimeoutError):
            err_type = "TIMEOUT"
        else:
            err_type = "HTTP_ERROR"
            
    if err_type is not None:
        raise ProviderUnavailable(err_type)
        
    decode_fail = False
    
    if isinstance(res, dict):
        if "status" not in res or "body" not in res:
            decode_fail = True
        else:
            status = res["status"]
            body = res["body"]
    else:
        if hasattr(res, "status_code"):
            status = res.status_code
            try:
                body = res.json() if hasattr(res, "json") else res
            except Exception:
                decode_fail = True
        elif hasattr(res, "status"):
            status = res.status
            body = res
        else:
            decode_fail = True
            
    if decode_fail:
        raise ProviderUnavailable("MALFORMED_BODY")
        
    if status != 200:
        raise ProviderUnavailable("HTTP_ERROR")
        
    if not isinstance(body, dict):
        raise ProviderUnavailable("MALFORMED_BODY")
        
    def _scrub(obj):
        if isinstance(obj, dict):
            return {k: _scrub(v) for k, v in obj.items() 
                    if key not in str(k) and "Authorization" not in str(k)}
        elif isinstance(obj, list):
            return [_scrub(x) for x in obj]
        elif isinstance(obj, str):
            return obj.replace(key, "***").replace("Authorization", "***")
        return obj

    scrubbed = _scrub(body)
    if key in str(scrubbed) or "Authorization" in str(scrubbed):
        raise ProviderUnavailable("MALFORMED_BODY")
        
    return scrubbed

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

