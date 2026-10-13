import { useEffect, useRef, useState } from "react";

// Barcode scanning: the browser's BarcodeDetector where it supports retail
// barcodes (Chrome on Android), ZXing otherwise (iOS Safari, or Android builds
// whose detector lacks EAN/UPC). Camera access needs HTTPS, which
// `tailscale serve` provides; on plain HTTP the manual field still works.

type Detector = { detect: (src: CanvasImageSource) => Promise<{ rawValue: string }[]> };
type DetectorCtor = {
  new (opts: { formats: string[] }): Detector;
  getSupportedFormats?: () => Promise<string[]>;
};
declare global {
  interface Window {
    BarcodeDetector?: DetectorCtor;
  }
}

const FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e"];

// The default 640×480 stream is too soft for barcode bars; ask for HD from the
// back camera with continuous autofocus (ignored where unsupported).
const CAMERA: MediaStreamConstraints = {
  audio: false,
  video: {
    facingMode: { ideal: "environment" },
    width: { ideal: 1920 },
    height: { ideal: 1080 },
    advanced: [{ focusMode: "continuous" } as MediaTrackConstraintSet],
  },
};

/** Try the HD back camera first, then progressively plainer requests, since some phones reject the first. */
async function openCamera(): Promise<MediaStream> {
  const attempts: MediaStreamConstraints[] = [CAMERA, { video: { facingMode: "environment" } }, { video: true }];
  let last: unknown;
  for (const c of attempts) {
    try {
      return await navigator.mediaDevices.getUserMedia(c);
    } catch (e) {
      last = e;
      // Denied permission won't change with different settings.
      if (e instanceof Error && (e.name === "NotAllowedError" || e.name === "SecurityError")) throw e;
    }
  }
  throw last;
}

async function nativeDetector(): Promise<Detector | null> {
  const BD = window.BarcodeDetector;
  if (!BD) return null;
  try {
    const supported = (await BD.getSupportedFormats?.()) ?? FORMATS;
    const formats = FORMATS.filter((f) => supported.includes(f));
    return formats.length ? new BD({ formats }) : null;
  } catch {
    return null;
  }
}

export function Scanner({ onCode }: { onCode: (code: string) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  // Latest callback in a ref, so a parent re-render never restarts the camera.
  const onCodeRef = useRef(onCode);
  onCodeRef.current = onCode;

  useEffect(() => {
    let stopped = false;
    let stream: MediaStream | null = null;
    let timer = 0;
    let stopZxing: (() => void) | null = null;
    const found = (code: string) => {
      if (stopped) return;
      stopped = true;
      navigator.vibrate?.(40);
      onCodeRef.current(code);
    };

    async function start() {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        setError("Camera needs HTTPS (open the app through tailscale serve). Type the number instead.");
        return;
      }
      try {
        stream = await openCamera();
        if (stopped) {
          stream.getTracks().forEach((t) => t.stop()); // closed while the permission prompt was up
          return;
        }
        const el = video.current!;
        const detector = await nativeDetector();
        if (detector) {
          el.srcObject = stream;
          await el.play().catch(() => { /* autoplay attribute keeps it running */ });
          const tick = async () => {
            if (stopped) return;
            if (el.readyState >= 2) {
              try {
                const codes = await detector.detect(el);
                if (codes[0]?.rawValue) return found(codes[0].rawValue);
              } catch {
                /* frame not ready */
              }
            }
            timer = window.setTimeout(tick, 120);
          };
          tick();
        } else {
          const [{ BrowserMultiFormatReader }, { BarcodeFormat, DecodeHintType }] = await Promise.all([
            import("@zxing/browser"), import("@zxing/library"),
          ]);
          const hints = new Map<number, unknown>([
            [DecodeHintType.POSSIBLE_FORMATS, [BarcodeFormat.EAN_13, BarcodeFormat.EAN_8, BarcodeFormat.UPC_A, BarcodeFormat.UPC_E]],
            [DecodeHintType.TRY_HARDER, true],
          ]);
          const reader = new BrowserMultiFormatReader(hints, { delayBetweenScanAttempts: 100 });
          const controls = await reader.decodeFromStream(stream, el, (result) => {
            if (result) found(result.getText());
          });
          if (stopped) controls.stop();
          else stopZxing = () => controls.stop();
        }
      } catch (e) {
        if (stopped) return;
        const name = e instanceof Error ? e.name : "";
        setError(
          name === "NotAllowedError" ? "Camera permission is blocked. Long-press the app icon → App info → Permissions → Camera → Allow, then reopen. Or type the number instead."
          : name === "NotReadableError" ? "The camera is busy in another app. Close it and try again, or type the number instead."
          : name === "NotFoundError" ? "No camera found. Type the number instead."
          : `Couldn't start the camera (${name || "unknown"}: ${e instanceof Error ? e.message : String(e)}). Type the number instead.`,
        );
      }
    }
    start();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      stopZxing?.();
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  if (error) return <div className="card tight small muted">{error}</div>;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="scanner">
        <video ref={video} muted playsInline autoPlay />
        <div className="reticle" />
      </div>
      <p className="tiny muted" style={{ margin: 0, textAlign: "center" }}>
        Fill the box with the barcode, about 15 cm (6 in) away, in good light.
      </p>
    </div>
  );
}
