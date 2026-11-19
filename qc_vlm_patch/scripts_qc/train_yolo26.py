"""
QC Vision - YOLO26 training (GTX 1070 8GB ke liye).

  pip install -U ultralytics
  python train_yolo26.py --data datasets/qc/data.yaml --task detect --size s
  python train_yolo26.py --data datasets/qc_obb/data.yaml --task obb --size s      # tilted tools ke liye behtar
  python train_yolo26.py --data datasets/qc_seg/data.yaml --task segment --size s

Pehli baar yolo26*.pt apne aap download hota hai (internet chahiye).
"""
import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="data.yaml ka path")
    ap.add_argument("--task", choices=["detect", "obb", "segment"], default="detect")
    ap.add_argument("--size", choices=["n", "s", "m"], default="s")
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8, help="8GB VRAM par OOM aaye to 4 karo")
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default="models/qc.pt", help="best weights yahan copy honge")
    args = ap.parse_args()

    suffix = {"detect": "", "obb": "-obb", "segment": "-seg"}[args.task]
    weights = f"yolo26{args.size}{suffix}.pt"
    name = args.name or f"yolo26{args.size}{suffix}_qc"
    print(f"Training {weights} on {args.data}")

    model = YOLO(weights)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=0,
        workers=2,
        patience=40,
        close_mosaic=15,
        name=name,
        # Chhota dataset: augmentation zaroori. Axis-aligned boxes ke saath zyada rotation box ko phula deta hai,
        # isliye detect mein kam; obb/segment mein tool kisi bhi angle par ho sakta hai => poori rotation.
        degrees=10 if args.task == "detect" else 180,
        flipud=0.5,
        fliplr=0.5,
        scale=0.3,
        hsv_v=0.4,
    )

    best = Path(model.trainer.best)
    metrics = YOLO(str(best)).val(data=args.data)
    print(f"mAP50 = {metrics.box.map50:.3f} | mAP50-95 = {metrics.box.map:.3f}")
    for cls_id, value in zip(metrics.box.ap_class_index, metrics.box.ap50):
        print(f"  {metrics.names[int(cls_id)]}: AP50 = {value:.3f}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(best, out)
    print(f"Best weights copied to {out}")


if __name__ == "__main__":
    main()
