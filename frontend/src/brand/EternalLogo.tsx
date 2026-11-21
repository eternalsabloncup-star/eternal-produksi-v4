/** Official ETERNAL brand marks — assets under /brand (Owner LOGO ETERNAL 3). */

type Variant = "horizontal" | "mark";

type Props = {
  variant: Variant;
  className?: string;
  /** Decorative when adjacent text names the brand. */
  decorative?: boolean;
};

const SRC: Record<Variant, { src: string; srcSet?: string; alt: string }> = {
  horizontal: {
    src: "/brand/eternal-logo-horizontal.png",
    srcSet:
      "/brand/eternal-logo-horizontal.png 1x, /brand/eternal-logo-horizontal@2x.png 2x",
    alt: "ETERNAL",
  },
  mark: {
    src: "/brand/eternal-logo-mark.png",
    srcSet: "/brand/eternal-logo-mark.png 1x, /brand/eternal-logo-mark@2x.png 2x",
    alt: "ETERNAL",
  },
};

export function EternalLogo({
  variant,
  className,
  decorative = false,
}: Props) {
  const asset = SRC[variant];
  return (
    <img
      className={className}
      src={asset.src}
      srcSet={asset.srcSet}
      alt={decorative ? "" : asset.alt}
      aria-hidden={decorative || undefined}
      draggable={false}
      decoding="async"
    />
  );
}
