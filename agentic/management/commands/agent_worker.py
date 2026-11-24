"""
Agent worker: runs queued agent work and acts on store events without anyone asking.

    python manage.py agent_worker

Keep it running in its own terminal next to the web server (run.bat starts both).
"""

import time
import traceback

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from agentic import negotiation, shopping_agent, store_agent
from agentic.models import AgentRun, WorkerHeartbeat
from agentic.runs import enqueue_now, finish

HANDLERS = {
    "plan": shopping_agent.plan_project,
    "parse_delegation": shopping_agent.parse_delegation,
    "fulfil_delegations": shopping_agent.fulfil_delegations,
    "negotiate": negotiation.run_negotiation,
    "stock_check": store_agent.handle_stock_check,
    "briefing": store_agent.run_briefing,
    "scan": store_agent.scan_stock,
}


def claim_next():
    run = AgentRun.objects.filter(status=AgentRun.QUEUED).order_by("priority", "created", "id").first()
    if run is None:
        return None
    # Atomic claim: only one worker can move it from queued to running
    if AgentRun.objects.filter(pk=run.pk, status=AgentRun.QUEUED).update(status=AgentRun.RUNNING, started=timezone.now()):
        run.refresh_from_db()
        return run
    return None


def execute(run, stdout=None):
    handler = HANDLERS.get(run.kind)
    try:
        if handler is None:
            raise ValueError(f"unknown run kind {run.kind}")
        result = handler(run)
        finish(run, result)
    except Exception as err:  # noqa: BLE001  the worker must keep running
        for s in run.steps:
            if s["status"] == "running":
                s["status"] = "failed"
        finish(run, error=f"{type(err).__name__}: {err}")
        if stdout:
            stdout.write(traceback.format_exc())
    return run


class Command(BaseCommand):
    help = "Run the Shopping Agent and Store Agent background loop."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process the queue once and exit (for tests).")
        parser.add_argument("--no-warmup", action="store_true")

    def handle(self, *args, **options):
        scan_minutes = settings.AGENT_SCAN_MINUTES
        self.stdout.write(self.style.SUCCESS(
            f"Agent worker started (model {settings.OLLAMA_MODEL}; stock scan every {scan_minutes} min). Ctrl+C to stop."))
        if not options["no_warmup"] and not options["once"]:
            try:
                started = time.monotonic()
                from agentic import llm
                llm.warm_up()
                self.stdout.write(f"Model loaded in {time.monotonic() - started:.0f}s")
            except Exception as err:  # noqa: BLE001
                self.stdout.write(self.style.WARNING(f"Could not reach Ollama yet ({err}); will retry on each run."))
        # Runs left "running" by a previous crash go back to the queue
        AgentRun.objects.filter(status=AgentRun.RUNNING).update(status=AgentRun.QUEUED)
        last_housekeeping = 0.0
        while True:
            close_old_connections()
            beat = WorkerHeartbeat.get()
            beat.last_seen = timezone.now()
            now = time.monotonic()
            if now - last_housekeeping > 60:
                last_housekeeping = now
                shopping_agent.expire_mandates()
                if scan_minutes and (beat.last_scan is None or (timezone.now() - beat.last_scan).total_seconds() > scan_minutes * 60):
                    if beat.last_scan is not None:  # first scan happens one interval after start
                        enqueue_now("scan")
                    beat.last_scan = timezone.now()
            run = claim_next()
            beat.current = f"{run.kind} #{run.pk}" if run else ""
            beat.save()
            if run is None:
                if options["once"]:
                    return
                time.sleep(1)
                continue
            label = f"{run.kind} #{run.pk}" + (f" ({run.product.title})" if run.product else "")
            self.stdout.write(f"[{timezone.localtime():%H:%M:%S}] start  {label}")
            execute(run, self.stdout)
            self.stdout.write(f"[{timezone.localtime():%H:%M:%S}] {run.status:6} {label}"
                              + (f" | model {run.llm_seconds:.0f}s" if run.llm_seconds else "")
                              + (f" | {run.error}" if run.error else ""))
