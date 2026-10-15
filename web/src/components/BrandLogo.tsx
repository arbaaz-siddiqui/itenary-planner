/**
 * The GujjuTours wordmark: "gujju" in brand blue, "tours" in brand red, as on
 * the website header. `compact` is the single-letter mark for the icon rail.
 */
export function BrandLogo({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <span
        aria-label="GujjuTours"
        className="grid h-8 w-8 place-items-center rounded-lg bg-brand-red text-[15px] font-bold text-white"
      >
        g
      </span>
    );
  }
  return (
    <span aria-label="GujjuTours" className="text-[21px] font-bold leading-none tracking-tight">
      <span className="text-brand-blue">gujju</span>
      <span className="text-brand-red">tours</span>
    </span>
  );
}
