/**
 * The GujjuTours logo, as on the website header. `compact` is the single-letter mark for the icon rail.
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
  // The client's own logo, self-hosted (public/brand) so the app never
  // depends on their CDN. It reads on both themes as is.
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src="/brand/gujju-logo.svg" alt="GujjuTours" width={124} height={30} className="h-[30px] w-auto shrink-0 select-none" draggable={false} />
  );
}
