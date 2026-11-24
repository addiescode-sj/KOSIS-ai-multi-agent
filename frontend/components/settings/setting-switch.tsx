"use client";

import { Switch } from "radix-ui";

export function SettingSwitch({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <Switch.Root
      aria-label={label}
      checked={checked}
      onCheckedChange={onChange}
      className="relative h-6 w-11 cursor-pointer rounded-full bg-gray-300 transition-colors outline-none focus-visible:ring-4 focus-visible:ring-brand-100 data-[state=checked]:bg-brand-600"
    >
      <Switch.Thumb className="block size-5 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-5.5" />
    </Switch.Root>
  );
}
