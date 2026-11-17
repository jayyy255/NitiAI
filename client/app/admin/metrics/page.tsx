"use client"

import { useEffect, useState } from "react"
import { DynamicNavbar } from "@/components/dynamic-navbar"
import { ProtectedRoute } from "@/components/protected-route"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { getMetricsSummary, triggerIngestion, trainRankingWeights, MetricsSummary } from "@/lib/api"
import "@/app/dashboard/dashboard.css"

export default function MetricsPage() {
    const [data, setData] = useState<MetricsSummary | null>(null)
    const [error, setError] = useState<string | null>(null)
    const [hours, setHours] = useState(24)
    const [notice, setNotice] = useState<string | null>(null)

    const load = async () => {
        const res = await getMetricsSummary(hours)
        if (res.error) setError(res.error)
        else { setError(null); setData(res.data ?? null) }
    }

    useEffect(() => { load() }, [hours]) // eslint-disable-line react-hooks/exhaustive-deps

    return (
        <ProtectedRoute>
            <div className="dashboard-theme">
                <DynamicNavbar />
                <main className="min-h-screen bg-background pt-28 pb-20">
                    <div className="max-w-6xl mx-auto px-4 space-y-6">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <h1 className="text-3xl font-bold">Match Engine metrics</h1>
                            <div className="flex gap-2">
                                <select className="border rounded-md px-2 bg-background" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
                                    <option value={1}>1h</option><option value={24}>24h</option><option value={168}>7d</option>
                                </select>
                                <Button variant="outline" onClick={async () => { const r = await triggerIngestion(); setNotice(r.error || "Ingestion started") }}>Run ingestion</Button>
                                <Button variant="outline" onClick={async () => { const r = await trainRankingWeights(); setNotice(r.error || JSON.stringify(r.data)) }}>Retrain ranking</Button>
                            </div>
                        </div>
                        {notice && <p className="text-sm text-muted-foreground break-all">{notice}</p>}
                        {error && <p className="text-red-600">{error}</p>}
                        {data && (
                            <>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                    <Card className="p-4"><div className="text-sm text-muted-foreground">Active jobs</div><div className="text-2xl font-bold">{data.jobs.active}</div></Card>
                                    <Card className="p-4"><div className="text-sm text-muted-foreground">Pending embeddings</div><div className="text-2xl font-bold">{data.jobs.pending_embed}</div></Card>
                                    <Card className="p-4"><div className="text-sm text-muted-foreground">Events ({hours}h)</div><div className="text-2xl font-bold">{Object.values(data.events).reduce((a, b) => a + b, 0)}</div></Card>
                                    <Card className="p-4"><div className="text-sm text-muted-foreground">LLM / DB</div><div className="text-2xl font-bold">{data.health?.llm ? "on" : "off"} / {data.health?.db ? "on" : "off"}</div></Card>
                                </div>
                                <Card className="p-4 overflow-x-auto">
                                    <h2 className="font-semibold mb-2">Latency by route</h2>
                                    <table className="w-full text-sm">
                                        <thead><tr className="text-left text-muted-foreground"><th>Route</th><th>Requests</th><th>p50 ms</th><th>p95 ms</th><th>p99 ms</th><th>5xx</th></tr></thead>
                                        <tbody>{data.routes.map((r) => (
                                            <tr key={r.method + r.route} className="border-t border-border/40"><td>{r.method} {r.route}</td><td>{r.n}</td><td>{r.p50}</td><td>{r.p95}</td><td>{r.p99}</td><td>{r.errors}</td></tr>
                                        ))}</tbody>
                                    </table>
                                </Card>
                                {data.last_ingest && (
                                    <Card className="p-4 text-sm">
                                        <h2 className="font-semibold mb-2">Last ingestion</h2>
                                        <p>Started {new Date(data.last_ingest.started_at).toLocaleString()} · fetched {data.last_ingest.fetched} · inserted {data.last_ingest.inserted} · updated {data.last_ingest.updated} · expired {data.last_ingest.expired} · embedded {data.last_ingest.embedded} · errors {data.last_ingest.errors?.length ?? 0}</p>
                                    </Card>
                                )}
                                {data.eval && (
                                    <Card className="p-4 overflow-x-auto">
                                        <h2 className="font-semibold mb-2">Offline eval ({data.eval.run_at})</h2>
                                        <table className="w-full text-sm">
                                            <thead><tr className="text-left text-muted-foreground"><th>Mode</th><th>Recall@20</th><th>NDCG@10</th><th>p50 ms</th></tr></thead>
                                            <tbody>{Object.entries(data.eval.retrieval).map(([m, r]) => (
                                                <tr key={m} className="border-t border-border/40"><td>{m}</td><td>{r["recall@20"]}</td><td>{r["ndcg@10"]}</td><td>{r.p50_ms}</td></tr>
                                            ))}</tbody>
                                        </table>
                                    </Card>
                                )}
                            </>
                        )}
                    </div>
                </main>
            </div>
        </ProtectedRoute>
    )
}
