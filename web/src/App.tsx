import { Link, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import LotList from "./pages/LotList";
import LotDetail from "./pages/LotDetail";

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-white border-b border-slate-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link to="/" className="text-xl font-bold text-slate-900">
              🔭 AR Radar
            </Link>
            <nav className="flex gap-4 text-sm">
              <Link to="/" className="text-slate-600 hover:text-slate-900">
                Дашборд
              </Link>
              <Link to="/lots" className="text-slate-600 hover:text-slate-900">
                Лента лотов
              </Link>
            </nav>
          </div>
          <div className="text-xs text-slate-500">v0.1.0 · источник: ЕФРСБ</div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/lots" element={<LotList />} />
          <Route path="/lots/:id" element={<LotDetail />} />
        </Routes>
      </main>
    </div>
  );
}
