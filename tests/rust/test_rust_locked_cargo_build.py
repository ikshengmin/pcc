"""Offline package bytes / dependency prerequisites. These are not Rust build receipts."""
from dataclasses import replace
import gzip
import hashlib
import io
from pathlib import Path
import tarfile
import tomllib

import pytest

from pcc.frontends.rust import cargo_package_sources as sources
from pcc.frontends.rust.cargo_build_plan import CargoPlatform,Unit,plan_locked_library
from pcc.frontends.rust.cargo_package_sources import (
    CargoSourceError,REGISTRY,RootArchivePin,archive_identities,load_cargo_source_closure,verify_cargo_source_archives,
)
from scripts.ci_orjson_source_frontier import (
    PACKAGE_STORE,UPSTREAM_TREE,authenticated_package_sources,default_library_plan,initial_report,load_inputs,
)

LINUX=CargoPlatform("x86_64-unknown-linux-gnu")
WINDOWS=CargoPlatform("x86_64-pc-windows-msvc")


def _id(kind,raw):
    return hashlib.sha1(kind+b" "+str(len(raw)).encode()+b"\0"+raw).digest()


def _tree(files):
    nodes={}
    for path,(_,mode,data) in files.items():
        here=nodes
        *dirs,leaf=path.split("/")
        for key in dirs:
            here=here.setdefault(key,{})
        here[leaf]=(mode,data)
    def walk(table):
        entries={}
        for name,entry in table.items():
            data=(b"40000 "+name.encode()+b"\0"+walk(entry) if isinstance(entry,dict) else
                  (b"100755" if entry[0]&0o111 else b"100644")+b" "+name.encode()+b"\0"+_id(b"blob",entry[1]))
            entries[name+("/" if isinstance(entry,dict) else "")]=data
        return _id(b"tree",b"".join(entries[k] for k in sorted(entries)))
    return walk(nodes).hex()


def _archive(prefix,members):
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode="w",format=tarfile.PAX_FORMAT) as tar:
        for path,(kind,mode,raw) in members:
            item=tarfile.TarInfo(prefix+"/"+path)
            item.type=kind
            item.mode=mode
            item.size=len(raw)
            if kind in {tarfile.LNKTYPE,tarfile.SYMTYPE}:
                item.linkname="unloaded"
            tar.addfile(item,io.BytesIO(raw))
    return gzip.compress(buf.getvalue(),mtime=0)


def _reg(data,mode=0o644):
    return tarfile.REGTYPE,mode,data


def _store(tmp_path,root_tail="",registry=None,root_deps=(),extra_root=None):
    registry=registry or {}
    base=tmp_path/"store";base.mkdir()
    (base/"registry").mkdir()
    lock='version=4\n'
    def row(name,deps):
        return ("[[package]]\nname="+repr(name)+"\nversion='1.0.0'\n"
                +'dependencies=['+','.join(repr(s) for s in deps)+']\n')
    for name,(manifest,deps) in registry.items():
        pkg=f"[package]\nname='{name}'\nversion='1.0.0'\n"+manifest
        files={"Cargo.toml":_reg(pkg.encode()),"src/lib.rs":_reg(b"// original\n")}
        if "build=" in manifest and "build=false" not in manifest:
            files["build.rs"]=_reg(b"fn main() {}\n")
        raw=_archive(name+"-1.0.0",files.items())
        (base/"registry"/(name+"-1.0.0.crate")).write_bytes(raw)
        lock+=row(name,deps)+f"source='{REGISTRY}'\nchecksum='{hashlib.sha256(raw).hexdigest()}'\n"
    lock+=row("primary",root_deps)
    files={"Cargo.toml":_reg(("[package]\nname='primary'\nversion='1.0.0'\n"
                               "resolver='3'\n"+root_tail).encode()),"Cargo.lock":_reg(lock.encode()),
           "src/lib.rs":_reg(b"// unchanged primary\n"),**(extra_root or {})}
    raw=_archive("primary-revision",files.items())
    (base/"upstream.tar.gz").write_bytes(raw)
    pin=RootArchivePin("1"*40,_tree(files),hashlib.sha256(raw).hexdigest(),"primary-revision")
    return base,pin,{n:v[2] for n,v in files.items()}


def test_original_full_source_and_selected_unit_graph_not_execution():
    _,files=load_inputs()
    c=authenticated_package_sources(files)
    assert len(c.root.files)==585 and c.root_pin.tree==UPSTREAM_TREE
    assert len(c.packages)==23
    assert sum(len(p.files) for p in c.packages.values())==1729
    assert sum(sum(map(len,p.files.values())) for p in c.packages.values())==14930773
    assert all(c.root.files[name]==raw for name,raw in files.items())
    assert c.packages["pyo3-build-config"].build_script is None
    assert c.packages["pyo3-ffi"].links=="python"
    assert b"interpreter_config.to_cargo_dep_env()?" in c.packages["pyo3-ffi"].files["build.rs"]
    assert b"InterpreterConfig::from_cargo_dep_env()" in c.packages["pyo3-build-config"].files["src/lib.rs"]
    assert b'out_dir.join("host.rs")' in c.packages["target-lexicon"].files["build.rs"]
    assert b"pyo3_build_config::get()" in c.root.files["build.rs"]
    verify_cargo_source_archives(PACKAGE_STORE,archive_identities(c))
    default=default_library_plan(c)
    assert len(default.active_packages)==16 and len(default.units)==26
    unselected={"no-panic","syn","quote","proc-macro2","unicode-ident","gimli","unwinding"}
    assert set(default.inactive_locked_packages)==unselected
    assert {p.unit.package for p in default.units if p.unit.kind=="compile-build-script"}=={
        "orjson","pyo3-ffi","target-lexicon","zmij","libc"}
    records={p.unit:p for p in default.units}
    assert dict(records[Unit("target","orjson","compile-build-script")].externs)=={
        "pyo3_build_config":Unit("host","pyo3-build-config","library"),"cc":Unit("host","cc","library")}
    assert dict(records[Unit("target","orjson","library")].externs)["pyo3_ffi"]==Unit("target","pyo3-ffi","library")
    ((edge),)=default.metadata_routes
    assert (edge.provider,edge.consumer,edge.links)==(Unit("target","pyo3-ffi","run-build-script"),
                                                     Unit("target","orjson","run-build-script"),"python")
    assert edge.provider in records[edge.consumer].prerequisites
    completed=set()
    for p in default.units:
        assert set(p.prerequisites)<=completed
        completed.add(p.unit)
    assert all(u["status"]=="UNRUN" for u in default.record()["units"])
    assert not default.record()["direct_links_metadata_routes"][0]["metadata_observed"]
    r=initial_report()
    assert r["owned_cargo_graph"]["status"]==r["build_rs_execution"]["status"]=="UNRUN"
    assert r["project_accepted"] is r["build_script_executed"] is False
    nightly=plan_locked_library(c,host=LINUX,target=LINUX,features=("nightly",))
    assert len(nightly.units)==37 and len(nightly.active_packages)==21
    assert Unit("host","no-panic","proc-macro") in {p.unit for p in nightly.units}
    assert "no-panic" in {p.unit.package for p in nightly.units if p.unit.namespace=="host"}
    assert "unwinding" not in nightly.active_packages
    unwind=plan_locked_library(c,host=LINUX,target=LINUX,features=("unwind",))
    assert len(unwind.units)==28 and len(unwind.active_packages)==18
    assert {"gimli","unwinding"}<=set(unwind.active_packages)
    assert "no-panic" not in unwind.active_packages


@pytest.mark.parametrize("which",["archive","archive-same-size","tree","original-changed","original-missing",
                                  "original-not-bytes","missing","symlink","directory","reread"])
def test_only_exact_archives_and_original_overlaps_are_accepted(tmp_path,which):
    store,pin,files=_store(tmp_path,registry={"dep":("",())})
    leaf=store/"registry/dep-1.0.0.crate"
    if which=="tree": pin=replace(pin,tree="0"*40)
    elif which=="original-changed": files["src/lib.rs"]+=b"\r"
    elif which=="original-missing": files["no-source"]=b""
    elif which=="original-not-bytes": files["no-source"]=None
    elif which=="missing":leaf.unlink()
    elif which=="symlink":
        alt=leaf.with_suffix(".source");leaf.rename(alt);leaf.symlink_to(alt.name)
    elif which=="directory":leaf.unlink();leaf.mkdir()
    elif which=="reread":
        ids=archive_identities(load_cargo_source_closure(store,pin,original_files=files))
        leaf.write_bytes(leaf.read_bytes()+b"changed after planning")
        with pytest.raises(CargoSourceError,match="archive changed"):
            verify_cargo_source_archives(store,ids)
        return
    else:
        raw=leaf.read_bytes()
        leaf.write_bytes(raw+b"x" if which=="archive" else raw[:-1]+bytes([raw[-1]^1]))
    with pytest.raises(CargoSourceError):
        load_cargo_source_closure(store,pin,original_files=files)


@pytest.mark.parametrize(("members","message"),[
    ([("../x",_reg(b""))],"noncanonical"),([("a//b",_reg(b""))],"noncanonical"),
    ([("bad", (tarfile.SYMTYPE,0o777,b""))],"nonregular"),
    ([("bad", (tarfile.LNKTYPE,0o644,b""))],"nonregular"),
    ([("bad",(tarfile.FIFOTYPE,0o600,b""))],"nonregular"),
    ([("x",_reg(b"a")),("x",_reg(b"b"))],"duplicate"),
    ([("x/y",_reg(b"a")),("x",_reg(b"b"))],"collision"),
    ([("x",_reg(b"a")),("x/y",_reg(b"b"))],"parent directory"),
    ([("x",_reg(b"a",0o4644))],"unsafe"),
])
def test_reader_rejects_nonnormal_tar_members_without_extracting_them(tmp_path,members,message):
    store,pin,files=_store(tmp_path)
    raw=_archive(pin.prefix,members)
    (store/"upstream.tar.gz").write_bytes(raw)
    with pytest.raises(CargoSourceError,match=message):
        load_cargo_source_closure(store,replace(pin,sha256=hashlib.sha256(raw).hexdigest()),original_files=files)
    assert not (tmp_path/"x").exists()


@pytest.mark.parametrize("bound",["MAX_COMPRESSED_PACKAGE","MAX_COMPRESSED_CLOSURE","MAX_TAR_PACKAGE",
                                   "MAX_TAR_CLOSURE","MAX_MEMBER","MAX_MEMBERS","MAX_PACKAGES"])
def test_each_source_store_budget_is_an_independent_hard_failure(monkeypatch,tmp_path,bound):
    store,pin,files=_store(tmp_path,registry={"dep":("",())})
    monkeypatch.setattr(sources,bound,1)
    with pytest.raises(CargoSourceError):
        load_cargo_source_closure(store,pin,original_files=files)


def test_target_predicates_select_host_for_build_deps_and_split_features(tmp_path):
    manifest="""build='build.rs'
[dependencies.shared]
version='1'
default-features=false
features=['for_target']
[build-dependencies.shared]
version='1'
default-features=false
features=['for_host']
[target.'cfg(windows)'.build-dependencies]
windows_helper='1'
[target.'cfg(windows)'.dependencies]
target_windows='1'
[target.'cfg(unix)'.dependencies]
unix_helper='1'
"""
    registry={n:("",()) for n in ("windows_helper","target_windows","unix_helper")}
    registry["shared"]=("[features]\nfor_target=[]\nfor_host=[]\n",())
    store,pin,files=_store(tmp_path,manifest,registry,tuple(registry),{"build.rs":_reg(b"fn main() {}")})
    c=load_cargo_source_closure(store,pin,original_files=files)
    plan=plan_locked_library(c,host=WINDOWS,target=LINUX)
    u={p.unit:p for p in plan.units}
    assert u[Unit("target","shared","library")].features==("for_target",)
    assert u[Unit("host","shared","library")].features==("for_host",)
    b=u[Unit("target","primary","compile-build-script")]
    assert b.code_target==WINDOWS.triple and b.package_target==LINUX.triple
    assert dict(b.externs)["windows_helper"]==Unit("host","windows_helper","library")
    assert Unit("target","unix_helper","library") in u
    assert "target_windows" not in plan.active_packages


def test_links_only_from_immediate_normal_dependency_with_a_build_script(tmp_path):
    reg={"middle":("build='build.rs'\n[dependencies]\nproducer='1'\n",("producer",)),
         "producer":("links='native'\nbuild='build.rs'\n",())}
    store,pin,files=_store(tmp_path,"build='build.rs'\n[dependencies]\nmiddle='1'\n",reg,("middle",),
                           {"build.rs":_reg(b"fn main() {}")})
    c=load_cargo_source_closure(store,pin,original_files=files)
    p=plan_locked_library(c,host=LINUX,target=WINDOWS)
    assert [(e.provider.package,e.consumer.package,e.links) for e in p.metadata_routes]==[
        ("producer","middle","native")]
    root=next(n for n in p.units if n.unit==Unit("target","primary","run-build-script"))
    assert all(dep.package!="producer" for dep in root.prerequisites)
    assert root.code_target==LINUX.triple and root.package_target==WINDOWS.triple


def test_renamed_and_declared_library_names_are_real_externs(tmp_path):
    reg={"original":("[lib]\nname='defined_name'\n",()),"another":("",())}
    store,pin,files=_store(tmp_path,"[dependencies]\noriginal='1'\n"
                           "my_alias={package='another',version='1'}",reg,tuple(reg))
    c=load_cargo_source_closure(store,pin,original_files=files)
    p=plan_locked_library(c,host=LINUX,target=LINUX)
    lib=next(u for u in p.units if u.unit==Unit("target","primary","library"))
    assert dict(lib.externs)=={"defined_name":Unit("target","original","library"),
                               "my_alias":Unit("target","another","library")}


@pytest.mark.parametrize(("tail","reg","deps","features","message"),[
    ("[lib]\nproc-macro=true",{},(),(),"primary proc-macro"),
    ("[dependencies]\nabsent='1'",{},(),(),"does not match"),
    ("[features]\nwith=['absent/x']",{},(),("with",),"undeclared dependency"),
    ("[dependencies]\nother='2'",{"other":("",())},("other",),(),"does not match"),
    ("[dependencies]\nother='>=1'",{"other":("",())},("other",),(),"unsupported Cargo version"),
    ("[target.'cfg(custom_declared)'.dependencies]\nother='1'",{"other":("",())},("other",),(),
     "unsupported conditional"),
    ("[dependencies]\nother={git='https://invalid.example/unused'}",{"other":("",())},("other",),(),
     "unsupported primary dependency"),
    ("[features]\na=[]",{},(),("missing",),"does not have feature"),
    ("[dependencies]\nother='1'",{"other":("[dependencies]\nprimary='1'",("primary",))},("other",),(),
     "cyclic"),
])
def test_no_guessed_unit_versions_features_platforms_or_cycles(tmp_path,tail,reg,deps,features,message):
    store,pin,files=_store(tmp_path,tail,reg,deps)
    c=load_cargo_source_closure(store,pin,original_files=files)
    with pytest.raises(CargoSourceError,match=message):
        plan_locked_library(c,host=LINUX,target=LINUX,features=features)


def test_unknown_platform_does_not_claim_linux_just_because_host_is_linux(tmp_path):
    store,pin,files=_store(tmp_path)
    c=load_cargo_source_closure(store,pin,original_files=files)
    with pytest.raises(CargoSourceError,match="family"):
        plan_locked_library(c,host=CargoPlatform("custom-host-abi"),target=LINUX)
    with pytest.raises(TypeError):  # Family cannot be supplied independently of the triple.
        CargoPlatform("x86_64-pc-windows-msvc","unix")


def test_original_executable_mode_and_invalid_archive_stream_are_not_ignored(tmp_path):
    store,pin,files=_store(tmp_path,extra_root={"aux":_reg(b"same contents",0o755)})
    root=store/"upstream.tar.gz"
    assert load_cargo_source_closure(store,pin,original_files=files).root.files["aux"]==b"same contents"
    raw=_archive(pin.prefix,((p,_reg(b)) for p,b in files.items()))
    root.write_bytes(raw)
    with pytest.raises(CargoSourceError,match="Git tree"):
        load_cargo_source_closure(store,replace(pin,sha256=hashlib.sha256(raw).hexdigest()),original_files=files)
    for bad in (raw[:-3],b"not gzip",gzip.compress(gzip.decompress(raw)+b"x"),gzip.compress(b"X"*1200)):
        root.write_bytes(bad)
        with pytest.raises(CargoSourceError):
            load_cargo_source_closure(store,replace(pin,sha256=hashlib.sha256(bad).hexdigest()),original_files=files)
