import { MonitorSmartphone } from "lucide-react";

// Marks a setting the server does not keep yet (no API in the contract): it lives in this browser only
export function LocalBadge() {
  return (
    <span className="ml-2 inline-flex items-center gap-1 rounded-md bg-gray-100 px-1.5 py-0.5 align-middle text-[11px] font-medium text-sub">
      <MonitorSmartphone size={11} />이 기기에만 저장
    </span>
  );
}
