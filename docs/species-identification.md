# Species identification extension

The authorized extension adds offline, reviewable SpeciesNet suggestions to the existing extraction workflow. Dashboard ecological analytics remain on hold.

## Processing

The implementation uses SpeciesNet package 5.0.6 and the official always-crop model 4.0.3a. Existing JAGUAR MegaDetector V6 MIT detections supply crop boxes; SpeciesNet's separate classifier and ensemble APIs process those boxes. No second detector is loaded. Each accepted detection receives its own classifier score vector and ensemble result. Inference runs one crop at a time on CPU.

The classifier runs in a separate Python interpreter because its YOLO dependency range differs from the existing detector stack. The packaged interpreter shares common runtime libraries through relative paths and supplies its own additional packages. Network connection and name-resolution APIs are blocked in that worker. A complete local manifest is required before model loading; remote model identifiers and missing assets are rejected. The model manifest's detector reference points to the local external detector asset, while info.upstream.json retains Google's original manifest. Model files remain unchanged.

The packaging directory supplies requirements-species.txt for the separate interpreter and prepare_species_models.py for a trusted official local archive. build_runtime.py accepts species-models and species-packages arguments together, adds the separate embedded interpreter, normalizes its external-detector manifest reference for installation, and records the extra dependencies and asset hashes. The development trial reused the existing CPU libraries through an isolated environment; the assembled runtime uses installation-relative paths rather than development-machine paths.

The processing stage retains full classifier results, all returned candidate labels/scores, ensemble output, previously unknown output fields, detection IDs and boxes, model/package/output-format versions, asset hashes, country settings, errors, and run history. Source hashes are checked before and after processing. Resume identity includes model hashes, settings, detection revisions, and image country assignments. Failed/latest detection prerequisites are not silently replaced with earlier runs.

Images without accepted detections remain explicit review cases and can receive manual annotations. SpeciesNet cannot recover an animal that the crop detector missed in this integration. Automatic human confirmation, unique-individual counting, age/sex inference, and ecological analytics are absent.

## Review and locations

Processing contains a Species identification tab. The Species page provides image inspection, per-detection suggestions and alternatives, confirmation, manual correction, unidentified status, and clearing of confirmations. Image-level manual annotations support missed animals and multiple labels. Reviewer and reason are required; every change preserves history and an audit event. Confirmed labels remain separate from model suggestions and are never overwritten by reruns.

Project settings supply an optional default country. Per-image assignments override it and retain audit evidence. Saudi Arabia uses SAU; Dubai is part of the United Arab Emirates and uses ARE. Mixed-country projects need the appropriate assignment for each image. No country is inferred from filenames, and unrelated sample images are not assigned these countries automatically. Other countries can use the project ISO alpha-3 setting. State filtering is limited to the upstream USA option.

## Excel

Analysis Data retains one row per image and adds suggestion summaries, confirmed labels/scientific names, identification count, review-needed status, source record ID, and actual country filter used. Species Identifications supplies historical per-detection predictions and taxonomy. Species Candidates retains every returned class score. Species Review History retains every human action. Species Raw losslessly chunks the complete processing payload, including future fields and failures. Existing source/evidence sheets remain available. Scores are model outputs rather than calibrated guarantees.

## Validation limits

The unfiltered seven-image trial completed without processing failures. Five accepted detections each returned 2,498 candidate scores; two detector-missed images produced no classification and remained review cases. The feral goat received a domestic-goat suggestion and the human received a human label. The sheep received Bovidae family, the Sandgrouse received a generic animal result, and the supplied Arabian Tahr label disagreed with a domestic-goat suggestion. These results establish integration behavior, not broad species accuracy. Every suggestion starts unreviewed. Larger verified regional datasets, night images, mixed-species scenes, and physical GPU validation remain outstanding.

The downloaded classifier label list does not contain Arabian tahr, Barbary sheep/Ammotragus, sandgrouse, or Hume's wheatear. Its wheatear entry is northern wheatear. Geographic filtering cannot introduce a species absent from that label list. Exact regional identifications therefore require manual annotation or a separately evaluated regional classifier/fine-tuning stage. These limitations remain visible in the preview documentation rather than being hidden by an automatic acceptance threshold.

The official model archive was downloaded from https://www.kaggle.com/api/v1/models/google/speciesnet/pyTorch/v4.0.3a/1/download. Official implementation/API references are https://github.com/google/cameratrapai and its advanced_topics.md. The model archive declares Apache-2.0. Existing transitive package redistribution obligations and unsigned-preview limitations still apply.

generated by Alapaap.net - open mind open skies
