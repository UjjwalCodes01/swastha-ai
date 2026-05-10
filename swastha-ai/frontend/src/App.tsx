import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import SubmissionsQueue from './pages/SubmissionsQueue';
import SubmissionDetail from './pages/SubmissionDetail';
import Compliance from './pages/Compliance';
import Settings from './pages/Settings';
import Explainability from './pages/Explainability';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="submissions" element={<SubmissionsQueue />} />
          <Route path="submissions/:id" element={<SubmissionDetail />} />
          <Route path="compliance" element={<Compliance />} />
          <Route path="explainability" element={<Explainability />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
