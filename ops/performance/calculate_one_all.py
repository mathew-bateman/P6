"""One full-source calculation in a separate process, with no project restriction."""
import sys
from calculate_all_trial import trial
if __name__=='__main__':
 tag,code=sys.argv[1:]
 assert tag.replace('_','').isalnum() and code in ('p6_22','p6_23')
 trial(scope=False,source_filter=code,compare=False,artifact_prefix=tag,output_prefix=tag+'_'+code+'_calculate')
