#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static void *payload(PyObject *o){return *(void**)((char*)o+32);}
static int64_t capacity(PyObject *o){return *(int64_t*)((char*)o+24);}
static int is_inline(PyObject *o){return payload(o)==(void*)((char*)o+40);}
static int zero_tail(PyObject *o){
  void **p=(void**)((char*)o+40);
  for(int i=0;i<4;i++)if(p[i]!=NULL)return 0;
  return 1;
}
int main(int ac,char **av){
 if(ac!=2)return 1;
 int expected=atoi(av[1]);
 PyObject *roots[4]={NULL,NULL,NULL,NULL};int32_t map[1]={4};
 pcc_gc_frame_enter(map,roots);
 roots[0]=py_list_new(0);
 if(!roots[0])return 2;
 pcc_gc_store_root(&roots[0],NULL); // ensure fixed runtime configuration before checking layout
 int requested[6]={0,1,2,4,5,8};
 for(int i=0;i<6;i++){
  int cap=requested[i]<4?4:requested[i];
  roots[0]=py_list_new(requested[i]);
  if(!roots[0]||capacity(roots[0])!=cap||py_list_len(roots[0])!=0)return 3;
  int inline_now=expected&&cap==4;
  if(is_inline(roots[0])!=inline_now||!payload(roots[0]))return 4;
  if(pcc_gc_pointer_is_managed((PyObject*)payload(roots[0]))!=0)return 5;
  void *old=payload(roots[0]);
  for(int j=0;j<cap;j++)py_list_append(roots[0],py_int_from_i64(j));
  if(py_list_len(roots[0])!=cap||payload(roots[0])!=old)return 6;
  py_list_append(roots[0],py_int_from_i64(cap));
  if(py_list_len(roots[0])!=cap+1||capacity(roots[0])!=cap*2||payload(roots[0])==old)return 7;
  if(inline_now&&!zero_tail(roots[0]))return 8;
  for(int j=0;j<=cap;j++)if(py_list_get_i64(roots[0],j)!=j)return 9;
  pcc_gc_store_root(&roots[0],NULL);
 }
 roots[0]=py_list_new(0);
 roots[1]=py_list_new(8);
 if(!roots[0]||!roots[1])return 10;
 py_list_append(roots[0],py_int_from_i64(9));
 for(int i=0;i<5;i++)py_list_append(roots[1],py_int_from_i64(i));
 if(py_list_set_slice(roots[0],py_int_from_i64(0),py_int_from_i64(1),py_None,roots[1])!=0)return 11;
 if(py_list_len(roots[0])!=5||is_inline(roots[0])||py_err_occurred())return 12;
 if(expected&&!zero_tail(roots[0]))return 13;
 for(int i=0;i<5;i++)if(py_list_get_i64(roots[0],i)!=i)return 14;
 pcc_gc_store_root(&roots[1],NULL);pcc_gc_store_root(&roots[0],NULL);
 roots[0]=py_list_new(3);
 py_list_append(roots[0],py_int_from_i64(3));
 py_list_append(roots[0],py_int_from_i64(1));
 py_list_append(roots[0],py_int_from_i64(2));
 roots[1]=py_obj_sorted(roots[0]);
 if(!roots[1]||py_list_len(roots[1])!=3)return 15;
 for(int i=0;i<3;i++)if(py_list_get_i64(roots[1],i)!=i+1)return 16;
 pcc_gc_store_root(&roots[1],NULL);pcc_gc_store_root(&roots[0],NULL);
 roots[0]=py_list_new(0);
 py_list_append(roots[0],roots[0]);
 pcc_gc_store_root(&roots[0],NULL);
 if(pcc_gc_collect(1)<0||py_err_occurred())return 17;
 pcc_gc_frame_leave(roots);
 puts("INLINE_LIST_CONTRACT_OK");
 return 0;
}
