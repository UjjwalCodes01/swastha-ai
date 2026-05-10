import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { FileQuestion, ArrowLeft, LayoutDashboard } from 'lucide-react';

const NotFound = () => {
  const navigate = useNavigate();

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="text-center max-w-sm"
      >
        <div className="w-20 h-20 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center mx-auto mb-6">
          <FileQuestion size={36} className="text-primary" />
        </div>
        <h1 className="text-4xl font-extrabold text-foreground tracking-tight mb-2">404</h1>
        <h2 className="text-lg font-semibold text-foreground mb-3">Page not found</h2>
        <p className="text-sm text-muted-foreground mb-8 leading-relaxed">
          The page you're looking for doesn't exist or may have been moved.
        </p>
        <div className="flex items-center justify-center gap-3">
          <button
            onClick={() => navigate(-1)}
            className="flex items-center gap-2 px-4 py-2.5 border border-border rounded-xl text-sm font-medium text-foreground hover:bg-muted transition-colors"
          >
            <ArrowLeft size={16} />
            Go Back
          </button>
          <button
            onClick={() => navigate('/dashboard')}
            className="flex items-center gap-2 px-4 py-2.5 bg-primary text-primary-foreground rounded-xl text-sm font-medium hover:bg-primary/90 transition-colors shadow-sm"
          >
            <LayoutDashboard size={16} />
            Dashboard
          </button>
        </div>
      </motion.div>
    </div>
  );
};

export default NotFound;
