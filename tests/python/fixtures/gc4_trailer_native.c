/* Isolated raw selector ABI test, not a managed collector or real stop owner.
 * Each live page owns a distinct mmap extent and 64-byte objects in its payload.
 * The retained-extent trailer is in a separate OS page so stale reads and writes
 * after simulated epoch loss fail, while live payload/registered scans remain.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "harness_globals.h"

extern int64_t pcc_gc_backend4_select_relocation_pages(int64_t);
extern int64_t pcc_gc_backend4_candidate_fresh_skips_count(void);
extern int64_t pcc_gc_backend4_relocation_add_refusals_count(void);
extern void *__real_malloc(size_t);
extern void __real_free(void *);

enum { PAGE_BYTES=128, NODE_BYTES=80, OBJECT_BYTES=64, TRAILER_BYTES=32,
       OS_PAGE=4096, MAX_PAGES=64, MAX_ALLOCS=8192 };
static unsigned char *pages, *nodes, *pages_before, *nodes_before;
static unsigned char *spans[MAX_PAGES], *span_before[MAX_PAGES];
static unsigned char *poison_before[MAX_PAGES];
static size_t extents[MAX_PAGES];
static unsigned char initialized[MAX_PAGES], active[MAX_PAGES];
static unsigned char poisoned[MAX_PAGES];
static long page_count, owners_per_page, node_count, budget, threads;
static long pin_checks, polls, lock_depth, lock_errors, epoch_queries;
static long rank_allocs, rank_live, plan_live, plan_attempts, malloc_calls;
static long fail_at, fail_rank, free_errors, protect_errors, integrity_errors;
static long loss_fired, initialized_seen, active_seen, poison_count;
static int64_t epoch=11, replacement_epoch=-1;
static const char *shape, *mode, *loss;
static uint64_t allocation_hash=1469598103934665603ULL;
static uint64_t free_hash=1469598103934665603ULL;
struct allocation { void *pointer; size_t size; int live; };
static struct allocation allocations[MAX_ALLOCS];
static long allocation_count;
static long forced_failures, failed_ordinal, failed_size, preplan_unlocks;
static int64_t input_keys[MAX_PAGES];
static int custom_keys;
static int64_t key_for(long p){
 unsigned k=0; const uint64_t M=INT64_MAX;
 if(!strncmp(mode,"width-",6)){
  k=(unsigned)atoi(mode+6); if(k<1||k>62)_Exit(30);
  uint64_t v=1ULL<<k;
  uint64_t keys[10]={1,v-1,v,v+1,1ULL<<32,1ULL<<62,M,v+1,M,2};
  return (int64_t)keys[p%10];
 }
 if(!strncmp(mode,"early-",6)){
  k=(unsigned)atoi(mode+6); if(k<2||k>61)_Exit(31);
  uint64_t v=1ULL<<k, keys[7]={v+1,v-1,2,v+1,v,v-1,1};
  return (int64_t)keys[p%7];
 }
 int64_t keys[10]={1,INT64_MAX,0,-1,INT64_MIN,1LL<<32,1LL<<62,INT64_MAX-1,2,1};
 return keys[p%10];
}

static uint64_t u64(const void *p,int o){uint64_t v;memcpy(&v,(const char *)p+o,8);return v;}
static uint32_t u32(const void *p,int o){uint32_t v;memcpy(&v,(const char *)p+o,4);return v;}
static void w64(void *p,int o,uint64_t v){memcpy((char *)p+o,&v,8);}
static void w32(void *p,int o,uint32_t v){memcpy((char *)p+o,&v,4);}
static void *ptr(const void *p,int o){return (void *)(uintptr_t)u64(p,o);}
static void wp(void *p,int o,const void *v){w64(p,o,(uintptr_t)v);}
static uint64_t hash(uint64_t h,uint64_t x){return (h^x)*1099511628211ULL;}
static unsigned char *object(long i){return spans[i%page_count]+(i/page_count)*OBJECT_BYTES;}
static int is_zero(const unsigned char *p,size_t n){for(size_t i=0;i<n;i++)if(p[i])return 0;return 1;}
static int is_sentinel(const unsigned char *p,size_t n){for(size_t i=0;i<n;i++)if(p[i]!=0xa5)return 0;return 1;}

/* Observe only at the selector's existing ABI boundaries. No candidate hook,
 * helper export, counter increment, allocator call, or instrumentation is added
 * to the emitted selector. Observations are not all native memory operations.
 */
static void observe_records(void){
 for(long p=0;p<page_count;p++){
  if(poisoned[p])continue;
  unsigned char *r=spans[p]+extents[p];
  if(!initialized[p]&&u64(r,0)==0&&u64(r,8)==UINT64_MAX&&u64(r,16)==0&&u64(r,24)==0){
   initialized[p]=1;initialized_seen++;
  }
  if(initialized[p]&&!active[p]&&u64(r,0)!=0&&(int64_t)u64(r,8)>0){active[p]=1;active_seen++;}
 }
}

static void check_protected(void){
 if(!pages_before)return;
 for(long p=0;p<page_count;p++){
  const unsigned char *page=pages+p*PAGE_BYTES,*before=pages_before+p*PAGE_BYTES;
  for(int i=0;i<PAGE_BYTES;i++)if((i<108||i>=112)&&page[i]!=before[i])integrity_errors++;
  size_t payload_bytes=(size_t)owners_per_page*OBJECT_BYTES;
  for(size_t i=0;i<payload_bytes;i++){
   /* Flags are the only authorized payload writes in this selector. */
   if(i%OBJECT_BYTES>=12&&i%OBJECT_BYTES<16)continue;
   if(spans[p][i]!=span_before[p][i])integrity_errors++;
  }
  /* Compare all retained slack at every observation without a scalar scan. */
  if(memcmp(spans[p]+payload_bytes,span_before[p]+payload_bytes,extents[p]-payload_bytes)){
   for(size_t i=payload_bytes;i<extents[p];i++)
    if(spans[p][i]!=span_before[p][i])integrity_errors++;
  }
  if(!poisoned[p]){
   const unsigned char *r=spans[p]+extents[p];
   for(size_t i=TRAILER_BYTES;i<OS_PAGE;i++)if(r[i]!=span_before[p][extents[p]+i])integrity_errors++;
  }
 }
 if(memcmp(nodes,nodes_before,(size_t)node_count*NODE_BYTES))integrity_errors++;
}

static void lose_epoch(void){
 if(loss_fired)return;
 observe_records();check_protected();
 epoch=replacement_epoch;loss_fired=1;
 for(long p=0;p<page_count;p++){
  unsigned char *r=spans[p]+extents[p];
  memcpy(poison_before[p],r,OS_PAGE);
  if(mprotect(r,OS_PAGE,PROT_NONE))protect_errors++;
  else {poisoned[p]=1;poison_count++;}
 }
}

void *__wrap_malloc(size_t size){
 malloc_calls++;
 if(size==16)plan_attempts++;else rank_allocs++;
 if(!strcmp(loss,"plan")&&size==16&&plan_attempts==1)lose_epoch();
 allocation_hash=hash(allocation_hash,size);
 if((fail_at&&malloc_calls==fail_at)||(fail_rank&&size!=16)){
  forced_failures++;failed_ordinal=malloc_calls;failed_size=(long)size;allocation_hash=hash(allocation_hash,0);return NULL;
 }
 void *p=__real_malloc(size);
 allocation_hash=hash(allocation_hash,p!=NULL);
 if(p){
  if(allocation_count==MAX_ALLOCS){fprintf(stderr,"allocation capacity exceeded\n");_Exit(20);}
  allocations[allocation_count++]=(struct allocation){p,size,1};
  if(size==16)plan_live++;else rank_live++;
 }
 return p;
}

void __wrap_free(void *p){
 if(!p)return;
 for(long i=allocation_count;i>0;i--){
  struct allocation *a=&allocations[i-1];
  if(a->pointer==p&&a->live){
   a->live=0;free_hash=hash(free_hash,(uint64_t)i);
   if(a->size==16)plan_live--;else rank_live--;
   __real_free(p);return;
  }
 }
 /* In particular, free(span) and free(span+extent) are never legal. */
 free_errors++;
}

int64_t pcc_gc_object_is_address_pinned(void *p){pin_checks++;return (u32(p,12)&64)!=0;}
int64_t pcc_gc_backend4_relocate_copy_supported_tag(int64_t tag){return tag!=999;}
void pcc_gc_backend4_zpage_clear_active_page(void *p){(void)p;}
void *pcc_gc_forwarding_find(void *p){return u64(p,24)==1?p:NULL;}
int64_t pcc_gc_forwarding_target_exists(void *p){return u64(p,24)==2;}
void pcc_gc_relocation_member_note(void *p){(void)p;}
int64_t pcc_gc_config_ensure(void){return 4;}
void pcc_py_gc_minor_graph_lock(void){if(lock_depth++)lock_errors++;}
void pcc_py_gc_minor_graph_unlock(void){
 if(--lock_depth)lock_errors++;
 observe_records();
 if(pin_checks>0&&pcc_gc_backend4_selector_scan_owner==0&&plan_attempts==0&&active_seen>0)preplan_unlocks++;
 if(!loss_fired&&!strcmp(loss,"initialize")&&pin_checks==0&&initialized_seen>0&&initialized_seen<page_count)lose_epoch();
 if(!loss_fired&&!strcmp(loss,"cleanup")&&active_seen==page_count){
  long cleaned=0;
  for(long p=0;p<page_count;p++)if(is_zero(spans[p]+extents[p],TRAILER_BYTES))cleaned++;
  if(cleaned>0&&cleaned<page_count)lose_epoch();
 }
}
void pcc_thread_safepoint(void){
 polls++;if(lock_depth)lock_errors++;
 observe_records();check_protected();
 if(loss_fired)return;
 if(!strcmp(loss,"admit")&&pin_checks==0&&initialized_seen==0)lose_epoch();
 if(!strcmp(loss,"scan")&&pin_checks>0&&pcc_gc_backend4_selector_scan_owner==1&&pcc_gc_backend4_selector_scan_cursor)lose_epoch();
 if(!strcmp(loss,"sort")&&pin_checks>0&&pcc_gc_backend4_selector_scan_owner==0&&plan_attempts==0&&active_seen==page_count)lose_epoch();
 if(!strcmp(loss,"commit")&&pcc_gc_backend4_reseed_relocation_revision>0)lose_epoch();
}
int64_t pcc_current_thread_id(void){return 1;}
int64_t pcc_threads_enabled(void){return threads;}
int64_t pcc_thread_world_stop_epoch_held(void){epoch_queries++;return epoch;}

static int build_fixture(void){
 custom_keys=!strncmp(mode,"width-",6)||!strncmp(mode,"early-",6)||!strcmp(mode,"signed")||!strcmp(mode,"wrap")||!strcmp(mode,"updates");
 if(sysconf(_SC_PAGESIZE)!=OS_PAGE)return 21;
 pages=calloc(page_count?page_count:1,PAGE_BYTES);
 nodes=calloc(node_count?node_count:1,NODE_BYTES);
 pages_before=calloc(page_count?page_count:1,PAGE_BYTES);
 nodes_before=calloc(node_count?node_count:1,NODE_BYTES);
 if(!pages||!nodes||!pages_before||!nodes_before)return 22;
 for(long p=0;p<page_count;p++){
  size_t extent=!strcmp(shape,"retained")?65536:4096;
  unsigned char *mapping=mmap(NULL,extent+3*OS_PAGE,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
  if(mapping==MAP_FAILED)return 23;
  if(mprotect(mapping,OS_PAGE,PROT_NONE)||mprotect(mapping+extent+2*OS_PAGE,OS_PAGE,PROT_NONE))return 24;
  spans[p]=mapping+OS_PAGE;extents[p]=extent;
  memset(spans[p],0xa5,extent+OS_PAGE);
  span_before[p]=calloc(1,extent+OS_PAGE);poison_before[p]=calloc(1,OS_PAGE);
  if(!span_before[p]||!poison_before[p])return 25;
  for(long previous=0;previous<p;previous++){
   uintptr_t a=(uintptr_t)spans[p],b=(uintptr_t)spans[previous];
   if(a<b+extents[previous]+OS_PAGE&&b<a+extent+OS_PAGE)return 26;
  }
  void *page=pages+p*PAGE_BYTES;
  w64(page,8,owners_per_page*OBJECT_BYTES);w64(page,16,4096);w32(page,24,0);
  w64(page,32,owners_per_page);w64(page,64,owners_per_page*OBJECT_BYTES);
  wp(page,56,p+1<page_count?pages+(p+1)*PAGE_BYTES:NULL);
  wp(page,72,spans[p]);w64(page,80,extent);
  if(strcmp(mode,"tie")){w64(page,40,(p*7)%19);w64(page,48,p%3);}
  if(!strcmp(mode,"blocked")&&p%5==0)w64(page,88,1);
  if(!strcmp(mode,"preselected")&&p%3==0)w32(page,108,1);
  if(!strcmp(mode,"zero")){w64(page,8,4096);w64(page,40,0);w64(page,48,0);}
  if(custom_keys){w64(page,40,!strcmp(mode,"wrap")?INT64_MAX:0);w64(page,48,!strcmp(mode,"wrap")?2:0);input_keys[p]=key_for(p);}
 }
 pcc_gc_backend4_page_head=(uintptr_t)(page_count?pages:NULL);
 for(long i=0;i<node_count;i++){
  void *obj=object(i),*node=nodes+i*NODE_BYTES,*page=pages+(i%page_count)*PAGE_BYTES;
  memset(obj,0,OBJECT_BYTES);w32(obj,8,2);w64(obj,16,i+1);
  wp(node,0,obj);wp(node,8,page);w64(node,32,OBJECT_BYTES);
  void *old=(void *)(uintptr_t)pcc_gc_backend4_zpage_head;
  wp(node,16,old);if(old)wp(old,40,node);pcc_gc_backend4_zpage_head=(uintptr_t)node;
  old=ptr(page,112);wp(node,48,old);if(old)wp(old,56,node);wp(page,112,node);
  if(!strcmp(mode,"mixed")){
   uint32_t flags=0;
   if(i%7==0)flags|=16384;if(i%11==0)flags|=64;if(i%17==0)flags|=524288;
   if(i%19==0)flags|=2048;if(i%5==0)flags|=256;
   w32(obj,12,flags);if(i%13==0)w32(obj,8,999);
   if(i%21==0)w64(obj,24,1);else if(i%23==0)w64(obj,24,2);
   w64(node,72,(i*17)%50);
  }
  if(!strcmp(mode,"fresh"))w32(obj,12,16384); if(!strcmp(mode,"defer_mix")){if(i%4==0)w64(node,32,65537);else if(i%7==0)w32(obj,12,16384);}
  if(!strcmp(mode,"forward"))w64(obj,24,1);
  if(!strcmp(mode,"residual")&&i%4==0)w32(obj,12,16384);
  if(!strcmp(mode,"fresh_priority")){
   w64(node,72,(i*17)%50);
   if(i==node_count-1)w32(obj,12,16384);
  }
 }
 if(custom_keys){
  for(long i=0;i<node_count;i++){
   long p=i%page_count; uint64_t key=(uint64_t)input_keys[p];
   if(!strcmp(mode,"updates")){
    /* Registered scan runs from the last node to the first. */
    long visit=node_count-1-i;
    key=1;
    if(visit==1||visit==3||visit==5||visit==6||visit==10||visit==12||visit==18||visit==20)key=INT64_MAX;
    if(visit==2)key=1ULL<<32;
    if(visit==4)key=1ULL<<62;
   }
   void *page=pages+p*PAGE_BYTES,*node=nodes+i*NODE_BYTES;
   uint64_t base=u64(page,16)-u64(page,8);
   /* Unsigned C arithmetic constructs exact signed-i64 wrapping metadata. */
   w64(node,72,key-base-u64(page,40)-u64(page,48));
  }
 }
 /* Only the last live page is malformed: the earlier valid pages must not be
  * initialized before whole-chain admission succeeds. The real mmap extent is
  * still tracked separately; corrupted metadata never changes the test bounds.
  */
 if(page_count){
  void *last=pages+(page_count-1)*PAGE_BYTES;
  if(!strcmp(shape,"no_span"))wp(last,72,NULL);
  if(!strcmp(shape,"bad_capacity"))w64(last,16,5000);
  if(!strcmp(shape,"bad_extent"))w64(last,80,4095);
  if(!strcmp(shape,"bad_allocated"))w64(last,64,4097);
  if(!strcmp(shape,"bad_class"))w32(last,24,3);
  if(!strcmp(shape,"unaligned"))wp(last,72,spans[page_count-1]+1);
  if(!strcmp(shape,"overflow"))wp(last,72,(void *)(uintptr_t)(INT64_MAX-4095));
 }
 if(!strcmp(mode,"scanbusy"))pcc_gc_backend4_selector_scan_owner=77;
 if(!strcmp(mode,"pagebusy"))pcc_gc_backend4_selector_page_owner=77;
 if(!strcmp(mode,"resetbusy"))pcc_gc_backend4_relocation_reset_owner=77;
 if(!strcmp(mode,"reseedbusy"))pcc_gc_backend4_reseed_commit_owner=77;
 memcpy(pages_before,pages,(size_t)page_count*PAGE_BYTES);
 memcpy(nodes_before,nodes,(size_t)node_count*NODE_BYTES);
 for(long p=0;p<page_count;p++)memcpy(span_before[p],spans[p],extents[p]+OS_PAGE);
 return 0;
}

int main(int argc,char **argv){
 /* shape mode pages owners budget threads epoch fail_at fail_rank loss replacement */
 if(argc!=12)return 2;
 shape=argv[1];mode=argv[2];page_count=atol(argv[3]);owners_per_page=atol(argv[4]);budget=atol(argv[5]);
 threads=atol(argv[6]);epoch=atoll(argv[7]);fail_at=atol(argv[8]);fail_rank=atol(argv[9]);loss=argv[10];replacement_epoch=atoll(argv[11]);
 if(page_count<0||page_count>MAX_PAGES||owners_per_page<0||owners_per_page>32)return 3;
 node_count=page_count*owners_per_page;
 int code=build_fixture();if(code)return code;
 int64_t selected=pcc_gc_backend4_select_relocation_pages(budget),second=0;
 if(!strcmp(mode,"reentry")){
  for(long p=0;p<page_count;p++)if(!u32(pages+p*PAGE_BYTES,108)){w64(pages+p*PAGE_BYTES,40,100000-p);w64(pages_before+p*PAGE_BYTES,40,100000-p);break;}
  second=pcc_gc_backend4_select_relocation_pages(budget);
 }
 for(long p=0;p<page_count;p++)if(poisoned[p]){
  if(mprotect(spans[p]+extents[p],OS_PAGE,PROT_READ|PROT_WRITE))return 27;
  if(memcmp(poison_before[p],spans[p]+extents[p],OS_PAGE))integrity_errors++;
  poisoned[p]=0;
 }
 check_protected();
 long clean=0,sentinel=0,nonzero=0;
 for(long p=0;p<page_count;p++){
  unsigned char *r=spans[p]+extents[p];
  clean+=is_zero(r,TRAILER_BYTES);sentinel+=is_sentinel(r,TRAILER_BYTES);nonzero+=!is_zero(r,TRAILER_BYTES);
 }
 uint64_t rh=1469598103934665603ULL,ph=rh,fh=rh;
 long reloc_n=0,page_n=0,first_obj=-1,first_page=-1;long page_order[MAX_PAGES];
 for(void *r=(void *)(uintptr_t)pcc_gc_relocation_set_head;r;r=ptr(r,8)){
  long id=u64(ptr(r,0),16);rh=hash(rh,id);reloc_n++;first_obj=id;if(reloc_n>node_count)return 28;
 }
 for(void *r=(void *)(uintptr_t)pcc_gc_backend4_evacuation_page_head;r;r=ptr(r,8)){
  if(page_n>=page_count)return 29;
  long id=((unsigned char *)ptr(r,0)-pages)/PAGE_BYTES;ph=hash(ph,id);page_order[page_n]=id;page_n++;first_page=id;if(page_n>page_count)return 29;
 }
 for(long i=0;i<node_count;i++)fh=hash(fh,u32(object(i),12));
 printf("{\"page_order\":[");
 for(long i=0;i<page_n;i++)printf("%s%ld",i?",":"",page_order[i]);
 printf("],\"forced_failures\":%ld,\"failed_ordinal\":%ld,\"failed_size\":%ld,\"preplan_unlocks\":%ld,",forced_failures,failed_ordinal,failed_size,preplan_unlocks);
 printf("\"selected\":%lld,\"second\":%lld,\"reloc_n\":%ld,\"page_n\":%ld,\"reloc_hash\":%llu,\"page_hash\":%llu,\"flags_hash\":%llu,\"first_obj\":%ld,\"first_page\":%ld,",(long long)selected,(long long)second,reloc_n,page_n,(unsigned long long)rh,(unsigned long long)ph,(unsigned long long)fh,first_obj,first_page);
 printf("\"fresh\":%lld,\"refusals\":%lld,\"deferred\":%llu,\"deferred_bytes\":%llu,\"candidates\":%llu,\"candidate_bytes\":%llu,\"zpage_bytes\":%llu,\"revision\":%llu,\"page_revision\":%llu,",(long long)pcc_gc_backend4_candidate_fresh_skips_count(),(long long)pcc_gc_backend4_relocation_add_refusals_count(),(unsigned long long)pcc_gc_backend4_large_object_defers,(unsigned long long)pcc_gc_backend4_large_object_deferred_bytes_count,(unsigned long long)pcc_gc_backend4_evacuation_candidates,(unsigned long long)pcc_gc_backend4_evacuation_candidate_bytes_count,(unsigned long long)pcc_gc_backend4_evacuation_candidate_zpage_bytes_count,(unsigned long long)pcc_gc_backend4_reseed_relocation_revision,(unsigned long long)pcc_gc_backend4_reseed_page_revision);
 printf("\"small_candidates\":%llu,\"medium_candidates\":%llu,\"small_bytes\":%llu,\"medium_bytes\":%llu,\"small_zbytes\":%llu,\"medium_zbytes\":%llu,",(unsigned long long)pcc_gc_backend4_small_page_candidates,(unsigned long long)pcc_gc_backend4_medium_page_candidates,(unsigned long long)pcc_gc_backend4_small_page_candidate_bytes_count,(unsigned long long)pcc_gc_backend4_medium_page_candidate_bytes_count,(unsigned long long)pcc_gc_backend4_small_page_candidate_zpage_bytes_count,(unsigned long long)pcc_gc_backend4_medium_page_candidate_zpage_bytes_count);
 printf("\"pin_checks\":%ld,\"polls\":%ld,\"epoch_queries\":%ld,\"lock_errors\":%ld,\"rank_allocs\":%ld,\"rank_live\":%ld,\"plan_live\":%ld,\"malloc_calls\":%ld,\"plan_attempts\":%ld,\"allocation_hash\":%llu,\"free_hash\":%llu,",pin_checks,polls,epoch_queries,lock_errors,rank_allocs,rank_live,plan_live,malloc_calls,plan_attempts,(unsigned long long)allocation_hash,(unsigned long long)free_hash);
 printf("\"initialized_seen\":%ld,\"active_seen\":%ld,\"trailer_clean\":%ld,\"trailer_sentinel\":%ld,\"trailer_nonzero\":%ld,\"distinct_spans\":%ld,\"logical_capacity\":4096,\"retained_extent\":%zu,\"loss_fired\":%ld,\"poison_count\":%ld,\"final_epoch\":%lld,\"free_errors\":%ld,\"protect_errors\":%ld,\"integrity_errors\":%ld}\n",initialized_seen,active_seen,clean,sentinel,nonzero,page_count,page_count?extents[0]:0,loss_fired,poison_count,(long long)epoch,free_errors,protect_errors,integrity_errors);
 return lock_errors||lock_depth||rank_live||plan_live!=reloc_n+page_n||free_errors||protect_errors||integrity_errors?5:0;
}
