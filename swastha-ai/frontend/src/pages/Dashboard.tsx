import { motion } from 'framer-motion';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts';
import { Activity, Clock, ShieldAlert, CheckCircle2, TrendingUp, AlertTriangle } from 'lucide-react';

const data = [
  { name: 'Mon', submissions: 40, critical: 24, processed: 35 },
  { name: 'Tue', submissions: 30, critical: 13, processed: 40 },
  { name: 'Wed', submissions: 20, critical: 48, processed: 25 },
  { name: 'Thu', submissions: 27, critical: 39, processed: 30 },
  { name: 'Fri', submissions: 18, critical: 48, processed: 25 },
  { name: 'Sat', submissions: 23, critical: 38, processed: 20 },
  { name: 'Sun', submissions: 34, critical: 43, processed: 30 },
];

const StatCard = ({ title, value, change, icon: Icon, trend }: any) => (
  <motion.div 
    initial={{ opacity: 0, y: 20 }}
    animate={{ opacity: 1, y: 0 }}
    className="bg-card p-6 rounded-2xl border border-border shadow-sm hover:shadow-md transition-shadow"
  >
    <div className="flex items-center justify-between">
      <div>
        <p className="text-sm font-medium text-muted-foreground mb-1">{title}</p>
        <h3 className="text-3xl font-bold text-foreground tracking-tight">{value}</h3>
      </div>
      <div className={`p-3 rounded-xl ${trend === 'up' ? 'bg-primary/10 text-primary' : 'bg-red-500/10 text-red-500'}`}>
        <Icon size={24} />
      </div>
    </div>
    <div className="mt-4 flex items-center text-sm">
      <TrendingUp size={16} className={`mr-1 ${trend === 'up' ? 'text-primary' : 'text-red-500'}`} />
      <span className={`font-medium ${trend === 'up' ? 'text-primary' : 'text-red-500'}`}>{change}</span>
      <span className="text-muted-foreground ml-2">vs last week</span>
    </div>
  </motion.div>
);

const Dashboard = () => {
  return (
    <div className="space-y-6 pb-10">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Dashboard Overview</h1>
          <p className="text-muted-foreground mt-1">AI processing metrics and submission queue status.</p>
        </div>
        <div className="flex gap-3">
          <button className="px-4 py-2 bg-card border border-border rounded-lg text-sm font-medium text-foreground hover:bg-muted transition-colors shadow-sm">
            Download Report
          </button>
          <button className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors shadow-sm">
            Sync Portals
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard title="Total Processed" value="2,845" change="+12.5%" icon={Activity} trend="up" />
        <StatCard title="Pending Review" value="142" change="-4.2%" icon={Clock} trend="up" />
        <StatCard title="Critical SAEs" value="28" change="+18.1%" icon={ShieldAlert} trend="down" />
        <StatCard title="Auto-Approved" value="1,893" change="+8.4%" icon={CheckCircle2} trend="up" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
        {/* Main Chart */}
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="lg:col-span-2 bg-card p-6 rounded-2xl border border-border shadow-sm"
        >
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-lg font-semibold text-foreground">Submission Throughput</h3>
            <select className="bg-muted/50 border border-border rounded-lg px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/20">
              <option>Last 7 days</option>
              <option>Last 30 days</option>
            </select>
          </div>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorSubmissions" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0}/>
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
          <div className="flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.slice(0, 4)} layout="vertical" margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(var(--border))" />
                <XAxis type="number" axisLine={false} tickLine={false} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} />
                <YAxis dataKey="name" type="category" axisLine={false} tickLine={false} tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }} />
                <Tooltip 
                  cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
                  contentStyle={{ backgroundColor: 'hsl(var(--card))', borderRadius: '8px', border: '1px solid hsl(var(--border))' }}
                />
                <Bar dataKey="critical" fill="hsl(0 84% 60%)" radius={[0, 4, 4, 0]} barSize={20} />
                <Bar dataKey="processed" fill="hsl(var(--primary))" radius={[0, 4, 4, 0]} barSize={20} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </motion.div>
      </div>

      {/* Recent Alerts */}
      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
        className="mt-6 bg-card rounded-2xl border border-border shadow-sm overflow-hidden"
      >
        <div className="p-6 border-b border-border flex items-center justify-between">
          <h3 className="text-lg font-semibold text-foreground">Recent Compliance Alerts</h3>
          <button className="text-sm font-medium text-primary hover:underline">View All</button>
        </div>
        <div className="divide-y divide-border">
          {[1, 2, 3].map((_, i) => (
            <div key={i} className="p-4 hover:bg-muted/30 transition-colors flex items-start gap-4 cursor-pointer">
              <div className="mt-1 bg-red-500/10 p-2 rounded-full text-red-500 shrink-0">
                <AlertTriangle size={18} />
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <h4 className="font-medium text-foreground">DPDP-PII-LEAK detected in DOC-9824{i}</h4>
                  <span className="text-xs text-muted-foreground">{i + 1}h ago</span>
                </div>
                <p className="text-sm text-muted-foreground">Potential direct identifier remained after anonymisation: AADHAAR. Review required before report generation.</p>
              </div>
            </div>
          ))}
        </div>
      </motion.div>
    </div>
  );
};

export default Dashboard;
