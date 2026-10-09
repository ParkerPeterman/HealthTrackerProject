import { useEffect, useRef, useState } from "react";

// Barcode scanning: the browser's BarcodeDetector where it exists (Chrome on
// Android), ZXing as the fallback (iOS Safari). Camera access needs HTTPS,
// which `tailscale serve` provides; on plain HTTP the manual field still works.

type Detector = { detect: (src: CanvasImageSource) => Promise<{ rawValue: string }[]> };
declare global {
  interface Window {
    BarcodeDetector?: new (opts: { formats: string[] }) => Detector;
  }
}

export function Scanner({ onCode }: { onCode: (code: string) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const done = useRef(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let raf = 0;
    let stopZxing: (() => void) | null = null;
    const found = (code: string) => {
      if (done.current) return;
      done.current = true;
      navigator.vibrate?.(40);
      onCode(code);
    };

    async function start() {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        setError("Camera needs HTTPS (open the app through tailscale serve). Type the number instead.");
        return;
      }
      try {
        if (window.BarcodeDetector) {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
          const el = video.current!;
          el.srcObject = stream;
          await el.play();
          const detector = new window.BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e"] });
          const tick = async () => {
            if (done.current) return;
            try {
              const codes = await detector.detect(el);
              if (codes[0]?.rawValue) return found(codes[0].rawValue);
            } catch {
              /* frame not ready */
            }
            raf = requestAnimationFrame(tick);
          };
          tick();
        } else {
          const { BrowserMultiFormatReader } = await import("@zxing/browser");
          const reader = new BrowserMultiFormatReader();
          const controls = await reader.decodeFromVideoDevice(undefined, video.current!, (result) => {
            if (result) found(result.getText());
          });
          stopZxing = () => controls.stop();
        }
      } catch (e) {
        setError(e instanceof Error && e.name === "NotAllowedError"
          ? "Camera permission was denied. Type the number instead."
          : "Couldn't start the camera. Type the number instead.");
      }
    }
    start();
    return () => {
      done.current = true;
      cancelAnimationFrame(raf);
      stopZxing?.();
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [onCode]);

  if (error) return <div className="card tight small muted">{error}</div>;
  return (
    <div className="scanner">
      <video ref={video} muted playsInline />
      <div className="reticle" />
    </div>
  );
}
