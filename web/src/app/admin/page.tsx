"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  MessageSquare,
  MessagesSquare,
  Search,
  ThumbsUp,
  UserPlus,
  Users,
  Activity,
} from "lucide-react";
import { api } from "@/lib/api";
import {
  failureRate,
  formatCount,
  groupByService,
  relativeTime,
  satisfaction,
  searchSummary,
  toolLabel,
  type AdminOverview,
} from "@/lib/admin";
import { Select } from "@/components/Select";
import {
  BarList,
  ColumnChart,
  Empty,
  ErrorNote,
  FOCUS,
  KpiTile,
  Loading,
  PageHeader,
  Panel,
} from "@/components/admin/AdminUi";

const RANGES = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
] as const;
type Range = (typeof RANGES)[number]["value"];

export default function AdminOverviewPage() {
  const [range, setRange] = useState<Range>("30");
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    setData(null);
    api
      .adminOverview(Number(range))
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the overview"));
  }, [range]);

  useEffect(load, [load]);

  const header = (
    <PageHeader title="Overview" subtitle="Who is using the travel agent and what they search for.">
      <Select<Range>
        value={range}
        options={[...RANGES]}
        onChange={setRange}
        className="w-[170px]"
        id="overview-range"
      />
    </PageHeader>
  );

  if (error) {
    return (
      <>
        {header}
        <ErrorNote message={error} onRetry={load} />
      </>
    );
  }
  if (!data) {
    return (
      <>
        {header}
        <Loading label="Loading the overview" />
      </>
    );
  }

  const k = data.kpis;
  const days = data.daily.map((d) => d.day);
  const services = groupByService(data.searches_by_tool);
  const happy = satisfaction(k.thumbs_up, k.thumbs_down);

  return (
    <>
      {header}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiTile
          icon={Users}
          label="Users"
          value={formatCount(k.total_users)}
          note={`${formatCount(k.new_users)} new in this period`}
          spark={data.daily.map((d) => d.signups)}
        />
        <KpiTile
          icon={Activity}
          label="Active users"
          value={formatCount(k.active_users)}
          note="Sent at least one message"
          spark={data.daily.map((d) => d.active_users)}
        />
        <KpiTile
          icon={MessagesSquare}
          label="Messages sent"
          value={formatCount(k.turns)}
          note={`${formatCount(k.chats)} new chats`}
          spark={data.daily.map((d) => d.turns)}
        />
        <KpiTile
          icon={Search}
          label="Searches"
          value={formatCount(k.searches)}
          note={`${failureRate(k.searches, k.failed_searches)} failed`}
          spark={data.daily.map((d) => d.searches)}
        />
        <KpiTile
          icon={AlertTriangle}
          label="Failed searches"
          value={formatCount(k.failed_searches)}
          tone={k.failed_searches ? "bad" : "default"}
          note="Supplier or tool errors"
          spark={data.daily.map((d) => d.failed_searches)}
          sparkClass="text-brand-red"
        />
        <KpiTile
          icon={UserPlus}
          label="New users"
          value={formatCount(k.new_users)}
          note={`Of ${formatCount(k.total_users)} accounts`}
        />
        <KpiTile
          icon={MessageSquare}
          label="New chats"
          value={formatCount(k.chats)}
          note={k.chats ? `${(k.turns / k.chats).toFixed(1)} messages per chat` : "No chats yet"}
        />
        <KpiTile
          icon={ThumbsUp}
          label="Reply ratings"
          value={happy ?? "None"}
          tone={happy ? "good" : "default"}
          note={`${formatCount(k.thumbs_up)} good, ${formatCount(k.thumbs_down)} bad`}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Active users per day">
          <ColumnChart days={days} values={data.daily.map((d) => d.active_users)} label="Active users per day" />
        </Panel>
        <Panel title="Messages per day">
          <ColumnChart
            days={days}
            values={data.daily.map((d) => d.turns)}
            label="Messages per day"
            colorClass="bg-[#8c5fc6]"
          />
        </Panel>
        <Panel title="Searches per day" className="lg:col-span-2">
          <ColumnChart
            days={days}
            values={data.daily.map((d) => d.searches)}
            secondary={data.daily.map((d) => d.failed_searches)}
            secondaryLabel="Failed"
            label="Searches per day"
          />
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel
          title="Searches per service"
          action={
            <Link href="/admin/searches" className={`text-xs text-link hover:underline ${FOCUS}`}>
              All searches
            </Link>
          }
        >
          {services.length ? (
            <BarList
              rows={services.map((s) => ({ label: s.service, value: s.searches, failed: s.failed }))}
            />
          ) : (
            <Empty>No searches in this period.</Empty>
          )}
        </Panel>
        <Panel title="Top destinations">
          {data.top_destinations.length ? (
            <BarList
              colorClass="bg-[#e2477c]"
              rows={data.top_destinations.map((d) => ({ label: d.destination, value: d.searches }))}
            />
          ) : (
            <Empty>No destinations searched in this period.</Empty>
          )}
        </Panel>
      </div>

      <Panel
        title="Recent failed searches"
        className="mt-4"
        action={
          <Link href="/admin/searches?errors_only=true" className={`text-xs text-link hover:underline ${FOCUS}`}>
            See all failures
          </Link>
        }
      >
        {data.recent_failed.length ? (
          <ul className="divide-y divide-border">
            {data.recent_failed.map((f) => (
              <li key={f.id} className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:gap-4">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-fg">
                    {toolLabel(f.tool)}
                    <span className="font-normal text-fg-muted"> · {searchSummary(f)}</span>
                  </p>
                  <p className="mt-0.5 line-clamp-2 text-sm break-words text-brand-red">{f.error}</p>
                </div>
                <div className="flex shrink-0 items-center gap-3 text-xs text-fg-muted sm:flex-col sm:items-end sm:gap-1">
                  <Link href={`/admin/users/${f.user_id}`} className={`max-w-[220px] truncate text-link hover:underline ${FOCUS}`}>
                    {f.email}
                  </Link>
                  <span className="flex items-center gap-2">
                    {relativeTime(f.created_at)}
                    {f.session_id && (
                      <Link href={`/admin/chats/${f.session_id}`} className={`text-link hover:underline ${FOCUS}`}>
                        Open chat
                      </Link>
                    )}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No failed searches in this period.</Empty>
        )}
      </Panel>
    </>
  );
}
