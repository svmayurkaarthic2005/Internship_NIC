# OCR engine benchmark: EasyOCR vs Tesseract

| fixture | engine | CER | WER | seconds | RSS (MB) | status |
|---|---|---|---|---|---|---|
| clean_tamil | easyocr | 1.000 | 1.000 | 4.43 | 909.7 | FAIL: RuntimeError: Error(s) in loading state_dict for Model:
	size mismatch for Prediction.weight: copying a param with shape torch.Size([143, 512]) from checkpoint, the shape in current model is torch.Size([127, 512]).
	size mismatch for Prediction.bias: copying a param with shape torch.Size([143]) from checkpoint, the shape in current model is torch.Size([127]). |
| clean_english | easyocr | 1.000 | 1.000 | 4.43 | 899.8 | FAIL: RuntimeError: Error(s) in loading state_dict for Model:
	size mismatch for Prediction.weight: copying a param with shape torch.Size([143, 512]) from checkpoint, the shape in current model is torch.Size([127, 512]).
	size mismatch for Prediction.bias: copying a param with shape torch.Size([143]) from checkpoint, the shape in current model is torch.Size([127]). |
| mixed_tamil_english | easyocr | 1.000 | 1.000 | 4.19 | 900.8 | FAIL: RuntimeError: Error(s) in loading state_dict for Model:
	size mismatch for Prediction.weight: copying a param with shape torch.Size([143, 512]) from checkpoint, the shape in current model is torch.Size([127, 512]).
	size mismatch for Prediction.bias: copying a param with shape torch.Size([143]) from checkpoint, the shape in current model is torch.Size([127]). |
| table | easyocr | 1.000 | 1.000 | 4.49 | 900.8 | FAIL: RuntimeError: Error(s) in loading state_dict for Model:
	size mismatch for Prediction.weight: copying a param with shape torch.Size([143, 512]) from checkpoint, the shape in current model is torch.Size([127, 512]).
	size mismatch for Prediction.bias: copying a param with shape torch.Size([143]) from checkpoint, the shape in current model is torch.Size([127]). |
| degraded_low_res | easyocr | 1.000 | 1.000 | 4.48 | 900.8 | FAIL: RuntimeError: Error(s) in loading state_dict for Model:
	size mismatch for Prediction.weight: copying a param with shape torch.Size([143, 512]) from checkpoint, the shape in current model is torch.Size([127, 512]).
	size mismatch for Prediction.bias: copying a param with shape torch.Size([143]) from checkpoint, the shape in current model is torch.Size([127]). |
| clean_tamil | tesseract | 0.240 | 0.800 | 0.67 | 108.4 | OK |
| clean_english | tesseract | 0.016 | 0.000 | 0.51 | 108.6 | OK |
| mixed_tamil_english | tesseract | 0.148 | 0.333 | 0.62 | 108.7 | OK |
| table | tesseract | 0.099 | 0.238 | 0.43 | 109.3 | OK |
| degraded_low_res | tesseract | 0.170 | 0.333 | 0.49 | 109.4 | OK |

## Summary

| engine | avg CER | avg WER | avg sec/page | peak RSS (MB) | failures |
|---|---|---|---|---|---|
| easyocr | - | - | 4.41 | 909.7 | clean_tamil, clean_english, mixed_tamil_english, table, degraded_low_res |
| tesseract | 0.135 | 0.341 | 0.54 | 109.4 | none |

**Verdict on this fixture set:** Only **tesseract** produced usable results on this fixture set.


This verdict is scoped to the fixtures actually run. Synthetic renders are a proxy for real scans (no noise, skew, stamps or handwriting) -- re-run with real scanned SIS/government PDFs in `--fixtures-dir` before treating this as final.
