import { AdminGate } from "@/components/admin/admin-gate";
import { HomeScreen } from "@/components/admin/home-screen";

// The operator home: today's numbers and what needs a look (read in the browser from the operator app)
export default function Page() {
  return (
    <AdminGate>
      <HomeScreen />
    </AdminGate>
  );
}
