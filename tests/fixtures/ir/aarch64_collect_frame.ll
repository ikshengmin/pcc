; Exact frozen runtime pcc_gc_collect IR shape; no runtime is linked.
target triple = "arm64-apple-darwin"
declare external void @pcc_diagnostics_runtime_log_event_code(i32, i32, i64, i64, ptr)
@pcc_diagnostics_runtime_log_fast_state = external global i8
declare external i64 @pcc_gc_backend()
@pcc_gc_backend_selected = external global i8
declare external void @pcc_gc_begin_explicit_tracing_collect()
declare external i64 @pcc_gc_collect_tracing()
@pcc_gc_config_initialized = external global i8
declare external void @pcc_gc_end_explicit_tracing_collect()
@pcc_gc_explicit_collect_active = external global i8
declare external i64 @pcc_gc_external_resource_poll()
declare external void @pcc_gc_precise_stack_diff_if_requested()
declare external i64 @pcc_gc_reclaim_retired_index_tables()
declare external void @pcc_gc_record_explicit_pause(i64, i64)
declare external i64 @pcc_gc_step(i64)
declare external i64 @pcc_gc_sweep_owed()
declare external i64 @pcc_platform_monotonic_us()
declare external i64 @pcc_resume_world()
declare external i64 @pcc_stop_the_world()
declare external void @pcc_thread_safepoint()
@pcc_thread_stop_requested = external global i32
declare external i64 @py_gc_collect()
@unsafe.cstr.4094 = internal constant [6 x i8] c"\73\74\61\72\74\00"
@unsafe.cstr.4167 = internal constant [5 x i8] c"\73\74\6F\70\00"
declare void @user_py_obj__pcc_gc_fire_callbacks(ptr %phase, i64 %phase_len)
define external i64 @pcc_gc_collect(i32 %reason) "no-builtins" {
entry:
  %thread.safepoint.flag.4063.3 = load atomic i32, ptr @pcc_thread_stop_requested acquire, align 4
  %thread.safepoint.need.4064.4 = icmp ne i32 %thread.safepoint.flag.4063.3, 0
  br i1 %thread.safepoint.need.4064.4, label %thread.safepoint.slow.4065, label %thread.safepoint.cont.4066

thread.safepoint.slow.4065:
  call void () @pcc_thread_safepoint()
  br label %thread.safepoint.cont.4066

thread.safepoint.cont.4066:
  br label %inl.cfg.34.entry
inl.cfg.34.entry:
  br label %inl.cfg.34.thread.safepoint.cont.17
inl.cfg.34.thread.safepoint.cont.17:
  %inl.cfg.34.unsafe.addr.18.3 = getelementptr i8, ptr @pcc_gc_config_initialized, i64 0
  %inl.cfg.34.unsafe.load.i32.20.4 = load i32, ptr %inl.cfg.34.unsafe.addr.18.3, align 1
  %inl.cfg.34.unsafe.i32.to.i64.21.5 = sext i32 %inl.cfg.34.unsafe.load.i32.20.4 to i64
  %inl.cfg.34.icmp.22.6 = icmp eq i64 %inl.cfg.34.unsafe.i32.to.i64.21.5, 0
  br i1 %inl.cfg.34.icmp.22.6, label %inl.cfg.34.if.then.23, label %inl.cfg.34.if.else.24

inl.cfg.34.if.then.23:
  %inl.cfg.34.gc.backend.ready.27.7 = load i32, ptr @pcc_gc_config_initialized
  %inl.cfg.34.gc.backend.is_ready.28.8 = icmp ne i32 %inl.cfg.34.gc.backend.ready.27.7, 0
  br i1 %inl.cfg.34.gc.backend.is_ready.28.8, label %inl.cfg.34.gc.backend.fast.29, label %inl.cfg.34.gc.backend.slow.30

inl.cfg.34.if.else.24:
  br label %inl.cfg.34.if.end.25

inl.cfg.34.if.end.25:
  %inl.cfg.34.unsafe.addr.37.13 = getelementptr i8, ptr @pcc_gc_backend_selected, i64 0
  %inl.cfg.34.unsafe.load.i32.39.14 = load i32, ptr %inl.cfg.34.unsafe.addr.37.13, align 1
  %inl.cfg.34.unsafe.i32.to.i64.40.15 = sext i32 %inl.cfg.34.unsafe.load.i32.39.14 to i64
  br label %inl.cfg.34.continuation
inl.cfg.34.gc.backend.fast.29:
  %inl.cfg.34.gc.backend.selected.33.9 = load i32, ptr @pcc_gc_backend_selected
  %inl.cfg.34.gc.backend.selected.i64.34.10 = sext i32 %inl.cfg.34.gc.backend.selected.33.9 to i64
  br label %inl.cfg.34.gc.backend.join.31

inl.cfg.34.gc.backend.slow.30:
  %inl.cfg.34.gc.backend.parsed.35.11 = call i64 () @pcc_gc_backend()
  br label %inl.cfg.34.gc.backend.join.31

inl.cfg.34.gc.backend.join.31:
  %inl.cfg.34.gc.backend.36.12 = phi i64 [%inl.cfg.34.gc.backend.selected.i64.34.10, %inl.cfg.34.gc.backend.fast.29], [%inl.cfg.34.gc.backend.parsed.35.11, %inl.cfg.34.gc.backend.slow.30]
  br label %inl.cfg.34.continuation
inl.cfg.34.continuation:
  %gc_backend_fast_ret.4067.5 = phi i64 [ %inl.cfg.34.unsafe.i32.to.i64.40.15, %inl.cfg.34.if.end.25 ], [ %inl.cfg.34.gc.backend.36.12, %inl.cfg.34.gc.backend.join.31 ]
  %icmp.4069.8 = icmp ne i64 %gc_backend_fast_ret.4067.5, 0
  br i1 %icmp.4069.8, label %if.then.4070, label %if.else.4071

if.then.4070:
  %unsafe.addr.4073.9 = getelementptr i8, ptr @pcc_gc_explicit_collect_active, i64 0
  %unsafe.load.i32.4075.10 = load i32, ptr %unsafe.addr.4073.9, align 1
  %unsafe.i32.to.i64.4076.11 = sext i32 %unsafe.load.i32.4075.10 to i64
  %icmp.4077.12 = icmp ne i64 %unsafe.i32.to.i64.4076.11, 0
  br i1 %icmp.4077.12, label %if.then.4078, label %if.else.4079

if.else.4071:
  br label %if.end.4072

if.end.4072:
  %unsafe.addr.4081.13 = getelementptr i8, ptr @pcc_diagnostics_runtime_log_fast_state, i64 0
  %unsafe.load.i32.4083.14 = load i32, ptr %unsafe.addr.4081.13, align 1
  %unsafe.i32.to.i64.4084.15 = sext i32 %unsafe.load.i32.4083.14 to i64
  %icmp.4085.16 = icmp ne i64 %unsafe.i32.to.i64.4084.15, 0
  br i1 %icmp.4085.16, label %if.then.4086, label %if.else.4087

if.then.4078:
  ret i64 0

if.else.4079:
  br label %if.end.4080

if.end.4080:
  br label %if.end.4072

if.then.4086:
  %extern.trunc32.4089.17 = trunc i64 2 to i32
  %extern.trunc32.4090.18 = trunc i64 1 to i32
  %sext.4092.20 = sext i32 %reason to i64
  call void (i32, i32, i64, i64, ptr) @pcc_diagnostics_runtime_log_event_code(i32 %extern.trunc32.4089.17, i32 %extern.trunc32.4090.18, i64 %sext.4092.20, i64 %gc_backend_fast_ret.4067.5, ptr null)
  br label %if.end.4088

if.else.4087:
  br label %if.end.4088

if.end.4088:
  %.22 = getelementptr inbounds [6 x i8], ptr @unsafe.cstr.4094, i32 0, i32 0
  call void (ptr, i64) @user_py_obj__pcc_gc_fire_callbacks(ptr %.22, i64 5)
  %icmp.4096.25 = icmp eq i64 %gc_backend_fast_ret.4067.5, 0
  br i1 %icmp.4096.25, label %if.then.4097, label %if.else.4098

if.then.4097:
  %extern.pcc_platform_monotonic_us.ret.4100.26 = call i64 () @pcc_platform_monotonic_us()
  %extern.py_gc_collect.ret.4101.28 = call i64 () @py_gc_collect()
  %extern.pcc_platform_monotonic_us.ret.4103.30 = call i64 () @pcc_platform_monotonic_us()
  call void (i64, i64) @pcc_gc_record_explicit_pause(i64 %extern.pcc_platform_monotonic_us.ret.4100.26, i64 %extern.pcc_platform_monotonic_us.ret.4103.30)
  br label %if.end.4099

if.else.4098:
  %extern.pcc_stop_the_world.ret.4104.31 = call i64 () @pcc_stop_the_world()
  br label %while.cond.4105

if.end.4099:
  %collected.addr.23.phi.2 = phi i64 [ %extern.py_gc_collect.ret.4101.28, %if.then.4097 ], [ %collected.addr.23.phi.1, %while.end.4128 ]
  %extern.pcc_gc_external_resource_poll.ret.4166.69 = call i64 () @pcc_gc_external_resource_poll()
  %.70 = getelementptr inbounds [5 x i8], ptr @unsafe.cstr.4167, i32 0, i32 0
  call void (ptr, i64) @user_py_obj__pcc_gc_fire_callbacks(ptr %.70, i64 4)
  %unsafe.addr.4168.71 = getelementptr i8, ptr @pcc_diagnostics_runtime_log_fast_state, i64 0
  %unsafe.load.i32.4170.72 = load i32, ptr %unsafe.addr.4168.71, align 1
  %unsafe.i32.to.i64.4171.73 = sext i32 %unsafe.load.i32.4170.72 to i64
  %icmp.4172.74 = icmp ne i64 %unsafe.i32.to.i64.4171.73, 0
  br i1 %icmp.4172.74, label %if.then.4173, label %if.else.4174

while.cond.4105:
  %stw.addr.32.phi = phi i64 [ %extern.pcc_stop_the_world.ret.4104.31, %if.else.4098 ], [ %extern.pcc_stop_the_world.ret.4111.35, %thread.safepoint.cont.4115 ]
  %icmp.4110.34 = icmp ne i64 %stw.addr.32.phi, 0
  br i1 %icmp.4110.34, label %while.body.4106, label %while.end.4108

while.body.4106:
  call void () @pcc_thread_safepoint()
  %extern.pcc_stop_the_world.ret.4111.35 = call i64 () @pcc_stop_the_world()
  br label %while.latch.4107

while.latch.4107:
  %thread.safepoint.flag.4112.36 = load atomic i32, ptr @pcc_thread_stop_requested acquire, align 4
  %thread.safepoint.need.4113.37 = icmp ne i32 %thread.safepoint.flag.4112.36, 0
  br i1 %thread.safepoint.need.4113.37, label %thread.safepoint.slow.4114, label %thread.safepoint.cont.4115

while.end.4108:
  %extern.pcc_gc_reclaim_retired_index_tables.ret.4116.38 = call i64 () @pcc_gc_reclaim_retired_index_tables()
  %icmp.4118.40 = icmp eq i64 %gc_backend_fast_ret.4067.5, 3
  br i1 %icmp.4118.40, label %if.then.4122, label %cond.right.4119

thread.safepoint.slow.4114:
  call void () @pcc_thread_safepoint()
  br label %thread.safepoint.cont.4115

thread.safepoint.cont.4115:
  br label %while.cond.4105

cond.right.4119:
  %icmp.4121.42 = icmp eq i64 %gc_backend_fast_ret.4067.5, 4
  br i1 %icmp.4121.42, label %if.then.4122, label %if.else.4123

if.then.4122:
  call void () @pcc_gc_precise_stack_diff_if_requested()
  br label %if.end.4124

if.else.4123:
  br label %if.end.4124

if.end.4124:
  call void () @pcc_gc_begin_explicit_tracing_collect()
  br label %while.cond.4125

while.cond.4125:
  %collected.addr.23.phi.1 = phi i64 [ 0, %if.end.4124 ], [ %collected.addr.23.phi, %thread.safepoint.cont.4164 ]
  %draining.addr.44.phi.3 = phi i64 [ 1, %if.end.4124 ], [ %draining.addr.44.phi.2, %thread.safepoint.cont.4164 ]
  %idle_rounds.addr.43.phi.2 = phi i64 [ 0, %if.end.4124 ], [ %idle_rounds.addr.43.phi.1, %thread.safepoint.cont.4164 ]
  %icmp.4130.46 = icmp ne i64 %draining.addr.44.phi.3, 0
  br i1 %icmp.4130.46, label %while.body.4126, label %while.end.4128

while.body.4126:
  %extern.pcc_gc_sweep_owed.ret.4131.47 = call i64 () @pcc_gc_sweep_owed()
  %icmp.4132.48 = icmp ne i64 %extern.pcc_gc_sweep_owed.ret.4131.47, 0
  br i1 %icmp.4132.48, label %if.then.4133, label %if.else.4134

while.latch.4127:
  %thread.safepoint.flag.4161.66 = load atomic i32, ptr @pcc_thread_stop_requested acquire, align 4
  %thread.safepoint.need.4162.67 = icmp ne i32 %thread.safepoint.flag.4161.66, 0
  br i1 %thread.safepoint.need.4162.67, label %thread.safepoint.slow.4163, label %thread.safepoint.cont.4164

while.end.4128:
  call void () @pcc_gc_end_explicit_tracing_collect()
  %extern.pcc_resume_world.ret.4165.68 = call i64 () @pcc_resume_world()
  br label %if.end.4099

if.then.4133:
  %extern.pcc_gc_collect_tracing.ret.4136.49 = call i64 () @pcc_gc_collect_tracing()
  %add.4139.53 = add i64 %collected.addr.23.phi.1, %extern.pcc_gc_collect_tracing.ret.4136.49
  %icmp.4141.55 = icmp eq i64 %extern.pcc_gc_collect_tracing.ret.4136.49, 0
  br i1 %icmp.4141.55, label %cond.right.4142, label %if.else.4146

if.else.4134:
  %extern.pcc_gc_step.ret.4148.58 = call i64 (i64) @pcc_gc_step(i64 1024)
  %icmp.4150.61 = icmp sgt i64 %extern.pcc_gc_step.ret.4148.58, 0
  br i1 %icmp.4150.61, label %if.then.4151, label %if.else.4152

if.end.4135:
  %collected.addr.23.phi = phi i64 [ %add.4139.53, %if.end.4147 ], [ %collected.addr.23.phi.1, %if.end.4153 ]
  %draining.addr.44.phi.2 = phi i64 [ %draining.addr.44.phi.4, %if.end.4147 ], [ %draining.addr.44.phi.1, %if.end.4153 ]
  %idle_rounds.addr.43.phi.1 = phi i64 [ %idle_rounds.addr.43.phi.3, %if.end.4147 ], [ %idle_rounds.addr.43.phi, %if.end.4153 ]
  br label %while.latch.4127

cond.right.4142:
  %extern.pcc_gc_sweep_owed.ret.4143.56 = call i64 () @pcc_gc_sweep_owed()
  %icmp.4144.57 = icmp ne i64 %extern.pcc_gc_sweep_owed.ret.4143.56, 0
  br i1 %icmp.4144.57, label %if.then.4145, label %if.else.4146

if.then.4145:
  br label %if.end.4147

if.else.4146:
  br label %if.end.4147

if.end.4147:
  %draining.addr.44.phi.4 = phi i64 [ 0, %if.then.4145 ], [ %draining.addr.44.phi.3, %if.else.4146 ]
  %idle_rounds.addr.43.phi.3 = phi i64 [ %idle_rounds.addr.43.phi.2, %if.then.4145 ], [ 0, %if.else.4146 ]
  br label %if.end.4135

if.then.4151:
  br label %if.end.4153

if.else.4152:
  %add.4155.63 = add i64 %idle_rounds.addr.43.phi.2, 1
  %icmp.4157.65 = icmp sge i64 %add.4155.63, 4
  br i1 %icmp.4157.65, label %if.then.4158, label %if.else.4159

if.end.4153:
  %draining.addr.44.phi.1 = phi i64 [ %draining.addr.44.phi.3, %if.then.4151 ], [ %draining.addr.44.phi, %if.end.4160 ]
  %idle_rounds.addr.43.phi = phi i64 [ 0, %if.then.4151 ], [ %add.4155.63, %if.end.4160 ]
  br label %if.end.4135

if.then.4158:
  br label %if.end.4160

if.else.4159:
  br label %if.end.4160

if.end.4160:
  %draining.addr.44.phi = phi i64 [ 0, %if.then.4158 ], [ %draining.addr.44.phi.3, %if.else.4159 ]
  br label %if.end.4153

thread.safepoint.slow.4163:
  call void () @pcc_thread_safepoint()
  br label %thread.safepoint.cont.4164

thread.safepoint.cont.4164:
  br label %while.cond.4125

if.then.4173:
  %extern.trunc32.4176.75 = trunc i64 2 to i32
  %extern.trunc32.4177.76 = trunc i64 2 to i32
  call void (i32, i32, i64, i64, ptr) @pcc_diagnostics_runtime_log_event_code(i32 %extern.trunc32.4176.75, i32 %extern.trunc32.4177.76, i64 %collected.addr.23.phi.2, i64 %gc_backend_fast_ret.4067.5, ptr null)
  br label %if.end.4175

if.else.4174:
  br label %if.end.4175

if.end.4175:
  ret i64 %collected.addr.23.phi.2
}
