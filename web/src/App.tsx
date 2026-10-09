import { NavLink, Route, Routes } from "react-router-dom";
import { Icon } from "./components/ui";
import Body from "./pages/Body";
import Food from "./pages/Food";
import More from "./pages/More";
import Settings from "./pages/Settings";
import Today from "./pages/Today";
import Train from "./pages/Train";
import WorkoutDetail from "./pages/WorkoutDetail";
import Coach from "./pages/Coach";

const TABS = [
  { to: "/", label: "Today", icon: "today" },
  { to: "/food", label: "Food", icon: "food" },
  { to: "/train", label: "Train", icon: "train" },
  { to: "/body", label: "Body", icon: "body" },
  { to: "/more", label: "More", icon: "more" },
];

export default function App() {
  return (
    <>
      <main className="app">
        <Routes>
          <Route path="/" element={<Today />} />
          <Route path="/food" element={<Food />} />
          <Route path="/train" element={<Train />} />
          <Route path="/train/:id" element={<WorkoutDetail />} />
          <Route path="/body" element={<Body />} />
          <Route path="/more" element={<More />} />
          <Route path="/more/coach" element={<Coach />} />
          <Route path="/more/settings" element={<Settings />} />
          <Route path="*" element={<Today />} />
        </Routes>
      </main>
      <footer className="tabbar">
        <nav aria-label="Main">
          {TABS.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              <Icon name={t.icon} />
              {t.label}
            </NavLink>
          ))}
        </nav>
      </footer>
    </>
  );
}
