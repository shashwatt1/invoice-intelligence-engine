import { motion } from "framer-motion";
import { Outlet, useLocation } from "react-router-dom";

import { Sidebar } from "./sidebar";

/**
 * Application frame: fixed sidebar + content column. Each route change
 * gets a subtle fade/rise transition keyed on the pathname.
 */
export function AppShell() {
  const location = useLocation();

  return (
    <div className="min-h-screen">
      <Sidebar />
      <main className="pl-60 max-lg:pl-16">
        <motion.div
          key={location.pathname}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
          className="mx-auto max-w-6xl px-8 py-8 max-md:px-4"
        >
          <Outlet />
        </motion.div>
      </main>
    </div>
  );
}
