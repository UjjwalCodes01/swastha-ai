import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar,
} from 'recharts';
import {
  Activity, Clock, ShieldAlert, CheckCircle2, TrendingUp,
  AlertTriangle, RefreshCw, Loader2,
} from 'lucide-react';
import { fetchDashboardMetrics, type DashboardMetrics } from '../lib/api';

const StatCard = ({ title, value, change, icon: Icon, trend, loading }: any) => (
  <motion.div
    initial={{ opacity: 0, y: 20 }}
    animate={{ opacity: 1, y: 0 }}
    className="bg-card p-6 rounded-2xl border border-border shadow-sm hover:shadow-md transition-shadow"
  >
    <div className="flex items-center justify-between">
      <div>
        <p className="text-sm font-medium text-muted-foreground mb-1">{title}</p>
        {loading ? (
          <div className="h-9 w-24 bg-muted animate-pulse rounded-lg" />
        ) : (
          <h3 className="text-3xl font-bold text-foreground tracking-tight">
            {typeof value === 'number' ? value.toLocaleString() : value}
          </h3>
        )}
      </div>
      <div className={`p-3 rounded-xl ${trend === 'up' ? 'bg-primary/10 text-primary' : 'bg-red-500/10 text-red-500'}`}>
        <Icon size={24} />
      </div>
    </div>
    <div className="mt-4 flex items-center text-sm">
      <TrendingUp size={16} className={`mr-1 ${trend === 'up' ? 'text-primary' : 'text-red-500'}`} />
      <span className={`font-medium ${trend === 'up' ? 'text-primary' : 'text-red-500'}`}>{change}</span>
      <span className="text-muted-foreground ml-2">live from DB</span>
    </div>
  </motion.div>
);

const Dashboard = () => {
  const { data, isLoading, isError, refetch, isFetching } = useQuery<DashboardMetrics>({
    queryKey: ['dashboard-metrics'],
    queryFn: fetchDashboardMetrics,
    refetchInterval: 60_000, // auto-refresh every 60 seconds
  });

  return (
    <div className="space-y-6 pb-10">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Dashboard Overview</h1>
          <p className="text-muted-foreground mt-1">Live AI processing metrics from the database.</p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="px-4 py-2 bg-card border border-border rounded-lg text-sm font-medium text-foreground hover:bg-muted transition-colors shadow-sm flex items-center gap-2 disabled:opacity-50"
          >
            {isFetching ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Refresh
          </button>
        </div>
      </div>

      {isError && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 rounded-xl p-4 text-sm flex items-center gap-2">
          <AlertTriangle size={16} />
          <span>Failed to load dashboard metrics. Is the backend running?</span>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard title="Total Processed" value={data?.total_processed ?? '—'} change="from PostgreSQL" icon={Activity} trend="up" loading={isLoading} />
        <StatCard title="Pending Review" value={data?.pending_review ?? '—'} change="real-time" icon={Clock} trend="up" loading={isLoading} />
        <StatCard title="Critical SAEs" value={data?.critical_saes ?? '—'} change="last 30 days" icon={ShieldAlert} trend="down" loading={isLoading} />
        <StatCard title="Auto-Approved" value={data?.auto_approved ?? '—'} change="no human review" icon={CheckCircle2} trend="up" loading={isLoading} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
        {/* Throughput chart */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="lg:col-span-2 bg-card p-6 rounded-2xl border border-border shadow-sm"
        >
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-lg font-semibold text-foreground">Submission Throughput</h3>
            <span className="text-xs text-muted-foreground bg-muted px-2 py-1 rounded">Last 7 days</span>
          </div>
          <div className="h-[300px] min-h-[300px] w-full">
            {isLoading ? (
              <div className="h-full bg-muted/30 animate-pulse rounded-xl" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data?.throughput ?? []} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorSubmissions" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(var(--border))" />
                  <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} dy={10} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} />
                  <Tooltip
                    contentStyle={{ backgroundColor: 'hsl(var(--card))', borderRadius: '8px', border: '1px solid hsl(var(--border))', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                    itemStyle={{ color: 'hsl(var(--foreground))' }}
                  />
                  <Area type="monotone" dataKey="submissions" stroke="hsl(var(--primary))" strokeWidth={3} fillOpacity={1} fill="url(#colorSubmissions)" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </motion.div>

        {/* Priority Breakdown */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="bg-card p-6 rounded-2xl border border-border shadow-sm flex flex-col"
        >
          <h3 className="text-lg font-semibold text-foreground mb-6">Priority Breakdown</h3>
          <div className="flex-1 h-[200px] min-h-[200px]">
            {isLoading ? (
              <div className="h-full bg-muted/30 animate-pulse rounded-xl" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data?.priority_breakdown ?? []} layout="vertical" margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(var(--border))" />
                  <XAxis type="number" axisLine={false} tickLine={false} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} />
                  <YAxis dataKey="name" type="category" axisLine={false} tickLine={false} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} width={80} />
                  <Tooltip
                    cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
                    contentStyle={{ backgroundColor: 'hsl(var(--card))', borderRadius: '8px', border: '1px solid hsl(var(--border))' }}
                  />
                  <Bar dataKey="critical" fill="hsl(0 84% 60%)" radius={[0, 4, 4, 0]} barSize={16} name="Critical" />
                  <Bar dataKey="processed" fill="hsl(var(--primary))" radius={[0, 4, 4, 0]} barSize={16} name="Total" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </motion.div>
      </div>

      {/* Empty state for charts if no data */}
      {!isLoading && !isError && (!data?.throughput || data.throughput.length === 0) && (
        <div className="bg-card rounded-2xl border border-border shadow-sm p-12 text-center text-muted-foreground">
          <Activity size={32} className="mx-auto mb-3 opacity-40" />
          <p className="font-medium">No submissions yet</p>
          <p className="text-sm mt-1">Upload documents via the ingestion API to see live metrics here.</p>
        </div>
      )}
    </div>
  );
};

export default Dashboard;
