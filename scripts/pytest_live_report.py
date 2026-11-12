"""Opt-in durable pytest reports: -p scripts.pytest_live_report --pcc-live-report PATH.

The controller records each report as it arrives, including xdist reports.
Failure tracebacks survive a later outer watchdog or interrupted session.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import sys
import time

import pytest
from _pytest._io.saferepr import saferepr
from _pytest.subtests import SubtestReport

from scripts.ci_validation_inventory import serial


def pytest_addoption(parser):
    parser.addoption("--pcc-live-report", default=None, help="New JSONL report path")
    parser.addoption("--pcc-live-subtests", action="store_true", default=False,
                     help="Separate explicit subtest events for subtest-aware consumers")
    parser.addoption("--pcc-live-collection-witness", action="store_true", default=False,
                     help="Record each worker's before/after repository collection inventory")


def pytest_configure(config):
    path = config.getoption("--pcc-live-report")
    if config.getoption("--pcc-live-collection-witness"):
        if not path:
            raise pytest.UsageError("collection witnesses require --pcc-live-report")
        config.pluginmanager.register(CollectionWitness(config), "pcc-live-collection-witness")
    if path and not hasattr(config, "workerinput"):
        config.pluginmanager.register(LiveReport(Path(path), config), "pcc-live-report")


class CollectionWitness:
    """Observe the original hooks, without changing selection or evaluating gates."""

    def __init__(self, config):
        self.config = config
        self.value = None

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, items):
        # Same before/after shape as ci_validation_inventory. Retain actual
        # objects until all selection and xdist group spelling hooks finish.
        before = [(item, {
            "canonical_nodeid": item.nodeid,
            "integration": bool(item.get_closest_marker("integration")),
            "gates": [{"args": serial(mark.args), "kwargs": serial(mark.kwargs)}
                      for mark in item.iter_markers("pcc_gate")],
        }) for item in items]
        result = yield
        selected_ids = {id(item) for item in items}
        rows = [dict(row, nodeid=item.nodeid, selected=id(item) in selected_ids)
                for item, row in before]
        self.value = {
            "version": 1, "platform": sys.platform, "machine": platform.machine().lower(),
            "marker": self.config.getoption("markexpr"),
            "keyword": self.config.getoption("keyword"),
            "rows": rows, "selected": [item.nodeid for item in items],
        }
        return result

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        if hasattr(self.config, "workeroutput"):
            self.config.workeroutput["pcc_collection_witness"] = self.value
        else:
            reporter = self.config.pluginmanager.getplugin("pcc-live-report")
            if self.value is not None:
                reporter.write("collection_witness", worker="controller", witness=self.value)


class LiveReport:
    def __init__(self, path, config):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("x", encoding="utf-8")
        self.collections = set()
        self.subtest_counts = {}
        self.separate_subtests = config.getoption("--pcc-live-subtests")
        self.collection_witness = config.getoption("--pcc-live-collection-witness")
        self.write(
            "start",
            argv=list(config.invocation_params.args),
            root=str(config.rootpath),
            markexpr=config.getoption("markexpr"),
            keyword=config.getoption("keyword"),
            ignore=config.getoption("ignore") or [],
            ignore_glob=config.getoption("ignore_glob") or [],
            deselect=config.getoption("deselect") or [],
            collect_only=config.getoption("collectonly"),
            maxfail=config.getoption("maxfail"),
            separate_subtests=self.separate_subtests,
            collection_witness=self.collection_witness,
            workers=config.getoption("numprocesses", default=0),
            dist=config.getoption("dist", default="no"),
            platform=sys.platform,
            machine=platform.machine().lower(),
            lf=bool(config.getoption("lf", default=False)),
            stepwise=bool(config.getoption("stepwise", default=False)),
            stepwise_skip=bool(config.getoption("stepwise_skip", default=False)),
            inifile=str(config.inipath or ""),
            override_ini=config.getoption("override_ini") or [],
            pyargs=bool(config.getoption("pyargs", default=False)),
            noconftest=bool(config.getoption("noconftest", default=False)),
            confcutdir=str(config.getoption("confcutdir") or ""),
            source_manifest=os.environ.get("PCC_VALIDATION_SOURCE_MANIFEST", ""),
            validation_environment={
                name: os.environ.get(name, "")
                for name in (
                    "PCC_VALIDATION_SOURCE_MANIFEST",
                    "PCC_VALIDATION_INSTALLATION_SHA256",
                    "PCC1_BINARY",
                    "PCC_SOURCE_ROOT",
                    "PCC_REPO_ROOT",
                    "PCC_RUNTIME_ARCHIVE",
                )
            },
        )

    def write(self, event, **fields):
        self.stream.write(json.dumps({"event": event, "time": time.time(), **fields}) + "\n")
        self.stream.flush()
        if fields.get("outcome") == "failed" or event in {"finish", "internalerror", "interrupt", "worker_crash"}:
            os.fsync(self.stream.fileno())

    def pytest_runtest_logreport(self, report):
        fields = dict(
            nodeid=report.nodeid,
            when=report.when,
            outcome=report.outcome,
            duration=report.duration,
        )
        if report.failed or report.skipped:
            fields["longrepr"] = report.longreprtext
        if hasattr(report, "wasxfail"):
            fields["wasxfail"] = report.wasxfail
        if self.separate_subtests and isinstance(report, SubtestReport):
            # uv.lock pins pytest 9's real SubtestReport, also reconstructed by
            # xdist from its "SubTestReport" transport discriminator. Never
            # reinterpret duplicate ordinary call phases as passing subtests.
            index = self.subtest_counts.get(report.nodeid, 0) + 1
            self.subtest_counts[report.nodeid] = index
            fields.update(subtest_index=index, report_type="_pytest.subtests.SubtestReport",
                          subtest_context={"msg": report.context.msg,
                                           "kwargs": {key: saferepr(value) for key, value in report.context.kwargs.items()}})
            self.write("subtest", **fields)
        else:
            # Existing strict consumers count all raw reports and reject extra
            # phases. Do not hide subtests from a consumer that did not opt in.
            self.write("report", **fields)

    def pytest_collectreport(self, report):
        if report.failed:
            self.write(
                "collection",
                nodeid=report.nodeid,
                outcome=report.outcome,
                longrepr=report.longreprtext,
            )

    def record_collection(self, nodeids):
        identity = tuple(nodeids)
        if identity not in self.collections:
            self.collections.add(identity)
            self.write("collected", nodeids=list(identity))

    def pytest_collection_finish(self, session):
        if not session.config.getoption("numprocesses", default=0):
            self.record_collection([item.nodeid for item in session.items])

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        self.record_collection(ids)

    def pytest_deselected(self, items):
        self.write("deselected", nodeids=[item.nodeid for item in items])

    def pytest_internalerror(self, excrepr, excinfo):
        self.write("internalerror", exception_type=excinfo.typename, longrepr=str(excrepr))

    def pytest_keyboard_interrupt(self, excinfo):
        self.write("interrupt", exception_type=excinfo.typename, longrepr=str(excinfo.value))

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        if error is not None:
            self.write("worker_crash", worker=node.gateway.id, error=str(error))
        if getattr(self, "collection_witness", False):
            self.write("collection_witness", worker=node.gateway.id,
                       witness=getattr(node, "workeroutput", {}).get("pcc_collection_witness"))

    def pytest_sessionfinish(self, session, exitstatus):
        self.write(
            "finish",
            exitstatus=int(exitstatus),
            testscollected=session.testscollected,
            testsfailed=session.testsfailed,
            shouldstop=str(session.shouldstop) if session.shouldstop else "",
            shouldfail=str(session.shouldfail) if session.shouldfail else "",
        )

    def pytest_unconfigure(self, config):
        self.stream.close()
