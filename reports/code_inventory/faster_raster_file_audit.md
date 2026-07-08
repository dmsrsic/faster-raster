# FasterRaster File Audit

- Python files found: `86`
- Important expected files: `20`
- Important files found: `20`
- Syntax OK: `80`
- Syntax errors: `6`

## Important files

| Status | Path | Lines | SHA256 short |
| --- | --- | ---: | --- |
| `OK` | `scripts/live_stack_cook_terminal_v053.py` | 465 | `05cd2ba62cc4` |
| `OK` | `scripts/summarize_live_stack_quality.py` | 498 | `d8e0cd67eed4` |
| `OK` | `scripts/plan_no_auth_cook_queue.py` | 111 | `4ed5e264d5ea` |
| `OK` | `scripts/propose_adapter_promotion.py` | 100 | `17dc59eb1f6e` |
| `OK` | `scripts/select_live_test_candidates.py` | 81 | `e0d32a77ec2b` |
| `OK` | `scripts/render_cli_screenshots.py` | 165 | `0785921d31b2` |
| `OK` | `scripts/probe_atlas_source.py` | 126 | `bd24193066f9` |
| `OK` | `scripts/live_download_probe.py` | 227 | `f0fa9764171f` |
| `OK` | `scripts/daymet_ncss_probe.py` | 321 | `7b6d7da2a3bc` |
| `OK` | `scripts/daymet_single_pixel_probe.py` | 206 | `e9405add1a9c` |
| `OK` | `scripts/multi_source_stack_probe.py` | 282 | `e82ae2dd6bc8` |
| `OK` | `scripts/lint_source_atlas.py` | 103 | `7e7edb6000bf` |
| `OK` | `scripts/render_source_stack_report.py` | 115 | `e252299f3b8f` |
| `OK` | `scripts/plan_source_unlocks.py` | 93 | `da1fa8744760` |
| `OK` | `faster_raster/cli_app.py` | 450 | `85e9e9c130b7` |
| `OK` | `faster_raster/cli_render.py` | 225 | `c34cadbebb38` |
| `OK` | `faster_raster/cli_lingo.py` | 40 | `fd755a97e9cf` |
| `OK` | `faster_raster/user_toggles.py` | 102 | `d4fdda14dba7` |
| `OK` | `faster_raster/probe_core.py` | 232 | `61295f0e55c7` |
| `OK` | `faster_raster/auth_profiles.py` | 80 | `7d6db5d4e48f` |

## All Python programs

| Path | Lines | Syntax | Classes | Functions preview |
| --- | ---: | --- | --- | --- |
| `faster_raster/__init__.py` | 1 | `OK` | - | - |
| `faster_raster/adapters/__init__.py` | 6 | `OK` | - | - |
| `faster_raster/adapters/arcgis_imageserver.py` | 91 | `OK` | ArcgisImageServerAdapter | year_params, request_bbox_for_policy, plan |
| `faster_raster/adapters/base.py` | 13 | `OK` | SourceAdapter | plan |
| `faster_raster/adapters/cog.py` | 6 | `OK` | CogAdapter | plan |
| `faster_raster/adapters/generic_https_template.py` | 116 | `OK` | GenericHttpsTemplateAdapter | template_placeholders, validate_template_placeholders, plan |
| `faster_raster/adapters/stac.py` | 6 | `OK` | StacAdapter | plan |
| `faster_raster/adapters/thredds_ncss.py` | 110 | `OK` | ThreddsNcssAdapter | validate_probe_config, plan_probe_request |
| `faster_raster/adapters/usda_cdl.py` | 6 | `OK` | UsdaCdlAdapter | - |
| `faster_raster/auth_profiles.py` | 80 | `OK` | - | load_auth_profiles, walk_values, validate_auth_profile, validate_auth_profiles, redact_auth_profile, assert_no_live_authenticated_request |
| `faster_raster/cli.py` | 243 | `OK` | - | _print_json, validate, resolve_sources, plan_urls_command, plan_harmonization, validate_manifest_command, validate_harmonization_command, inspect_manifest |
| `faster_raster/cli_app.py` | 450 | `OK` | - | emit, load_context, version_command, doctor_command, sources_list, sources_tree, sources_show, sources_search |
| `faster_raster/cli_explore.py` | 102 | `OK` | ExploreResult | handle_slash_command, run_explore, source_rows |
| `faster_raster/cli_lingo.py` | 40 | `OK` | - | load_lingo, resolve_mode, title, glossary, glossary_text |
| `faster_raster/cli_models.py` | 231 | `OK` | CliPaths | read_json, read_yaml, load_atlas, load_sources, load_stack, load_matrix, load_unlocks, load_auth |
| `faster_raster/cli_render.py` | 225 | `OK` | - | stable_json, strip_ansi, status_label, shorten_text, table_plain, render_sources_plain, render_goods_plain, render_bads_plain |
| `faster_raster/contract.py` | 192 | `OK` | - | transform_status, inspect_contract, check_golden_fixtures |
| `faster_raster/crs.py` | 54 | `OK` | UnsupportedCRSTransform | normalize_crs, epsg_number, lonlat_to_web_mercator, web_mercator_to_lonlat, transform_bbox |
| `faster_raster/execution_package.py` | 443 | `OK` | - | sha256_file, sha256_text, write_json, load_execution_profile, file_extension_from_url, cache_key_for, content_addressed_path, build_failure_policy |
| `faster_raster/harmonization_planner.py` | 88 | `OK` | - | planned_output_for, build_harmonization_plan, write_harmonization_plan, read_harmonization_plan, summarize_harmonization_plan, plan_from_manifest |
| `faster_raster/manifest.py` | 36 | `OK` | - | write_manifest, read_manifest, summarize_manifest |
| `faster_raster/output_validation.py` | 315 | `OK` | - | _is_non_empty_string, _is_crs, _is_bbox, _is_positive_int, _url_is_https, _validate_semantic_resampling, _parse_manifest_jsonl, validate_manifest_rows |
| `faster_raster/probe_core.py` | 232 | `OK` | - | utc_now_iso, require_network_opt_in, redact_url, safe_headers, is_text_response, read_bounded_response, classify_probe_result, probe_http |
| `faster_raster/scheduler_export.py` | 118 | `OK` | - | write_text, write_job_index, slurm_script, local_dry_run_script, read_package, scheduler_summary, export_scheduler_package |
| `faster_raster/schema_export.py` | 422 | `OK` | - | string_schema, number_schema, integer_schema, bbox_schema, research_spec_schema, source_registry_schema, acquisition_manifest_row_schema, harmonization_plan_schema |
| `faster_raster/schemas.py` | 108 | `OK` | Project, Aoi, TargetGrid, SourceSpec | years_are_sorted_unique |
| `faster_raster/source_registry.py` | 25 | `OK` | - | load_registry, get_registry_entry |
| `faster_raster/tiling.py` | 103 | `OK` | - | bbox_from_geojson, _projected_span_m, _ceil_positive, plan_tiles, walk |
| `faster_raster/url_planner.py` | 29 | `OK` | - | plan_urls |
| `faster_raster/user_toggles.py` | 102 | `OK` | - | _normalize_scalar, _normalize_toggles, load_user_toggles, effective_toggles, walk_values, validate_user_toggles, write_effective_reports |
| `faster_raster/validation.py` | 112 | `OK` | - | load_spec, validate_spec, validate_or_raise |
| `scripts/daymet_access_surface_probe.py` | 202 | `ERROR: invalid non-printable character U+FEFF at line 1` | - | - |
| `scripts/daymet_ncss_probe.py` | 321 | `OK` | - | utc_now_iso, parse_args, load_probe_spec, safe_headers, read_bounded_response, is_unresolved, encode_subset_url, build_stage_url |
| `scripts/daymet_single_pixel_probe.py` | 206 | `OK` | - | utc_now_iso, parse_args, load_probe_spec, query_items, build_url, safe_headers, read_bounded_response, probe |
| `scripts/lint_source_atlas.py` | 103 | `OK` | - | walk_values, load_atlas, lint_atlas, summarize, write_reports, main |
| `scripts/live_download_probe.py` | 227 | `OK` | - | utc_now_iso, parse_args, looks_like_zip, validate_zip, write_json, md_value, write_markdown, run_probe |
| `scripts/live_stack_cook_terminal_v053.py` | 465 | `OK` | - | urlencode, cdl_export_url, daymet_single_pixel_url, tnm_products_url, gfs_subset_candidates, targets, mostly_text, text_preview |
| `scripts/live_url_structure_probe.py` | 224 | `OK` | - | utc_now_iso, parse_args, safe_headers, probe_one, write_report_json, write_report_md, main |
| `scripts/multi_source_stack_probe.py` | 282 | `ERROR: invalid non-printable character U+FEFF at line 1` | - | - |
| `scripts/plan_no_auth_cook_queue.py` | 111 | `OK` | - | load_yaml, endpoint_present, candidate_status, score_entry, build_queue, write_reports, main |
| `scripts/plan_source_unlocks.py` | 93 | `OK` | - | load_yaml, classify, score, plan_unlocks, recommendation, write_reports, main |
| `scripts/probe_atlas_source.py` | 126 | `OK` | - | load_atlas, find_source, policy_check, run_atlas_probe, write_reports, main |
| `scripts/propose_adapter_promotion.py` | 100 | `OK` | - | load_yaml, find_source, latest_probe, expected_adapter, decision, build_proposal, write_reports, main |
| `scripts/render_cli_screenshots.py` | 165 | `OK` | - | write_scene, table, scene_pantry, scene_sauce_prism, scene_recipe, scene_gridmet, scene_batcher, scene_goods_bads |
| `scripts/render_source_stack_report.py` | 115 | `OK` | - | load_yaml, next_unlock, build_matrix, group_name, write_outputs, main |
| `scripts/run_diagnostics.py` | 627 | `OK` | Timer | sha256_file, run_pytest_durations, presence_check, synthetic_spec, mixed_synthetic_spec, adapter_counts, run_synthetic_performance, run_mixed_planning_benchmark |
| `scripts/select_live_test_candidates.py` | 81 | `OK` | - | load_pack, classify, select_candidates, write_reports, main |
| `scripts/summarize_live_stack_quality.py` | 498 | `OK` | GradeBand | read_json, grade, pct, clamp, content_family, result_quality_label, row_recommendation, score_report |
| `tests/conftest.py` | 33 | `OK` | - | project_spec_path, valid_spec, valid_spec_raw, registry |
| `tests/test_auth_profiles.py` | 41 | `OK` | - | test_example_profiles_validate, test_rejects_raw_secret_looking_values, test_redacts_secret_references, test_scaffold_profiles_cannot_execute_authenticated_requests |
| `tests/test_capability_validation.py` | 66 | `OK` | - | registry_mutation, test_unsupported_adapter_rejected_before_planning, test_missing_bboxsr_support_rejected_before_planning, test_missing_url_param_names_rejected_before_planning, test_unsupported_year_strategy_rejected_before_planning, test_unsupported_bbox_transform_rejected_before_planning, test_capability_validation_runs_before_url_rows |
| `tests/test_cli_app.py` | 73 | `OK` | - | invoke, test_command_registration_and_version, test_sources_list_plain_and_json, test_sources_show_and_search, test_stack_summary_and_unlocks_next, test_auth_profile_redaction, test_probe_atlas_dry_run_gridmet, test_live_probe_refuses_without_allow_network |
| `tests/test_cli_cook_toggles.py` | 52 | `OK` | - | ok, test_toggles_and_knobs_commands, test_cook_queue_and_aliases, test_cook_dip_gridmet_dry_run_and_json, test_cook_live_refuses_when_network_mode_off, test_cook_proposal_gridmet |
| `tests/test_cli_explore.py` | 21 | `OK` | - | test_explore_help_and_exit, test_explore_sources_source_stack_unlocks, test_explore_probe_dry_run |
| `tests/test_cli_formatting_v053.py` | 57 | `OK` | - | ok, test_shortener_rewrites_repeated_phrases, test_pantry_compact_and_wide_modes_are_available, test_goods_default_excludes_duplicate_guard_and_include_guards_adds_it, test_source_scope_and_scope_alias, test_endpoint_readiness_cli_json_and_plain |
| `tests/test_cli_integration.py` | 160 | `OK` | - | test_cli_validate_success, test_cli_resolve_sources_summary, test_cli_plan_urls_writes_manifest_and_summary, test_cli_plan_harmonization_writes_plan_and_summary, test_cli_inspect_manifest_prints_summary, test_cli_inspect_harmonization_prints_summary, test_cli_invalid_input_returns_nonzero, test_cli_inspect_contract_passes_for_example |
| `tests/test_cli_kitchen_aliases.py` | 88 | `ERROR: invalid non-printable character U+FEFF at line 1` | - | - |
| `tests/test_cli_lingo.py` | 28 | `ERROR: invalid non-printable character U+FEFF at line 1` | - | - |
| `tests/test_cli_models.py` | 29 | `OK` | - | test_load_sources_and_summary, test_filter_and_search_sources, test_auth_rows_are_redacted, test_gridmet_dry_run_classification |
| `tests/test_cli_render.py` | 24 | `OK` | - | test_stable_json_parseable_no_markup, test_plain_table_no_ansi, test_help_style_contains_statuses |
| `tests/test_daymet_ncss_design.py` | 86 | `OK` | - | load_probe_spec, test_daymet_probe_spec_yaml_parses, test_thredds_ncss_adapter_is_disabled_by_default, test_thredds_ncss_probe_request_is_deterministic, test_thredds_ncss_probe_request_has_expected_fields, test_thredds_ncss_validation_errors_are_clear, test_thredds_ncss_adapter_does_not_use_network, fail_network |
| `tests/test_daymet_ncss_probe_script.py` | 163 | `OK` | FakeHeaders, FakeResponse | load_script_module, test_script_refuses_without_allow_network, test_spec_loads, test_request_descriptor_is_deterministic, test_read_bounded_response_enforces_max_bytes, test_report_writers_produce_json_and_markdown, test_no_network_guard_with_unresolved_metadata_endpoint, test_mocked_metadata_probe_can_pass |
| `tests/test_daymet_single_pixel_probe.py` | 112 | `OK` | FakeHeaders, FakeResponse | load_script_module, test_url_construction_is_deterministic, test_refuses_without_allow_network, test_max_bytes_cap_helper, test_report_writers_shape, test_no_network_with_mocked_opener, items, __init__ |
| `tests/test_docs_python3.py` | 9 | `OK` | - | test_docs_use_python3_for_json_tool |
| `tests/test_execution_package.py` | 266 | `OK` | - | read_json, assert_package_files, compile_package, test_valid_ohio_cdl_package_generation, test_valid_generic_https_package_generation, test_mixed_arcgis_generic_package_generation, test_full_four_stage_dag_generation, test_dependency_correctness_and_no_cycles |
| `tests/test_generic_https_template.py` | 115 | `OK` | - | generic_spec_raw, test_generic_url_template_byte_stability, test_generic_placeholder_replacement_correctness, test_generic_unknown_placeholder_fails_clearly, test_generic_missing_url_template_fails_clearly, test_generic_continuous_bilinear_resampling_accepted, test_generic_categorical_bilinear_rejected, test_harmonization_accepts_generic_rows |
| `tests/test_golden_contract.py` | 123 | `OK` | - | generate_manifest_and_plan, test_generated_preserve_bbox_manifest_matches_golden, test_generated_project_bbox_manifest_matches_golden, test_generated_harmonization_plans_match_golden, test_golden_manifest_url_params_match_expected, test_golden_manifest_rows_have_explicit_contract_fields, test_golden_harmonization_request_ids_match_manifest_ids, test_generated_generic_https_manifest_matches_golden |
| `tests/test_harmonization_planning.py` | 90 | `OK` | - | test_harmonization_plan_is_deterministic, test_golden_harmonization_plan_bytes_are_stable, test_every_manifest_request_id_appears_once_in_harmonization_inputs, test_harmonization_inputs_include_manifest_contract_fields, test_target_crs_is_epsg5070_for_example, test_categorical_source_uses_nearest_only, test_forbidden_resampling_includes_unsafe_methods, test_planned_output_paths_are_deterministic |
| `tests/test_multi_source_stack_probe.py` | 113 | `ERROR: invalid non-printable character U+FEFF at line 1` | - | - |
| `tests/test_output_validation.py` | 129 | `OK` | - | test_valid_current_ohio_manifest_passes, test_valid_current_ohio_harmonization_passes_with_manifest, test_malformed_jsonl_fails, test_duplicate_request_id_fails, test_missing_url_fails, test_invalid_crs_field_fails, test_categorical_bilinear_rejected_in_manifest, test_manifest_to_plan_mismatch_fails |
| `tests/test_plan_no_auth_cook_queue.py` | 28 | `OK` | - | test_no_auth_cook_queue_prioritizes_safe_sources, test_gridmet_marked_endpoint_uncertainty, test_cook_queue_reports_write |
| `tests/test_plan_source_unlocks.py` | 31 | `OK` | - | test_unlock_plan_has_ranked_items, test_unlock_classes_include_adapter_or_auth, test_unlock_reports_write |
| `tests/test_probe_atlas_source.py` | 87 | `OK` | FakeHeaders, FakeResponse | load_module, test_gridmet_is_blocked_by_endpoint_uncertainty, test_refuses_credentialed_source, test_mocked_safe_source_probe_passes, test_report_writers, items, __init__, __enter__ |
| `tests/test_probe_core.py` | 71 | `OK` | FakeHeaders, FakeResponse | test_requires_network_opt_in, test_bounded_probe_text_preview_and_sha, test_partial_content_classification, test_classification_rules, test_url_redaction, test_stable_json_is_deterministic, items, __init__ |
| `tests/test_propose_adapter_promotion.py` | 22 | `OK` | - | test_gridmet_promotion_proposal_not_ready, test_promotion_reports_write |
| `tests/test_real_raster_url_structures.py` | 104 | `OK` | - | load_case, test_nlcd_tile_url_exact_documented_structure, test_nlcd_mosaic_url_exact_documented_structure, test_prism_daily_url_exact_documented_structure, test_real_template_missing_source_specific_field_fails_clearly, test_nlcd_categorical_rejects_unsafe_resampling, test_prism_continuous_bilinear_permitted, test_real_template_no_network_access |
| `tests/test_render_cli_screenshots.py` | 36 | `ERROR: invalid non-printable character U+FEFF at line 1` | - | - |
| `tests/test_render_source_stack_report.py` | 27 | `OK` | - | test_build_matrix_shape, test_grouping_and_outputs |
| `tests/test_schema_export.py` | 111 | `OK` | - | load_schema, assert_required_fields, test_schema_export_writes_expected_files, test_schema_export_is_byte_stable, test_committed_schema_files_are_current, test_schemas_have_required_fields_and_enum_like_contracts, test_current_examples_match_schema_required_fields, test_schema_structural_status_passes |
| `tests/test_select_live_test_candidates.py` | 22 | `OK` | - | test_select_live_test_candidates_fail_closed_without_endpoints, test_live_candidate_reports_write |
| `tests/test_source_atlas_lint.py` | 35 | `OK` | - | test_source_atlas_parses_and_has_25_entries, test_source_atlas_lints_clean, test_linter_catches_credential_without_profile, test_linter_catches_direct_url_without_endpoint |
| `tests/test_source_registry.py` | 61 | `OK` | - | test_source_registry_loads, test_usda_cdl_registry_entry_resolves, test_missing_registry_key_gives_clear_error, test_adapter_type_is_required, test_url_parameter_field_names_are_present |
| `tests/test_spec_validation.py` | 63 | `OK` | - | test_valid_research_spec_passes, test_required_schema_fields_fail, test_invalid_categorical_resampling_fails, test_years_are_normalized_deterministically_in_planning, test_duplicate_years_fail_schema |
| `tests/test_tiling.py` | 85 | `OK` | - | write_bbox, test_aoi_smaller_than_max_size_has_one_tile, test_aoi_requiring_width_split, test_aoi_requiring_height_split, test_aoi_requiring_width_and_height_split |
| `tests/test_url_planning.py` | 185 | `OK` | - | registry_with_policy, parsed_params, test_manifest_crs_contract_fields_are_explicit, test_preserve_input_bbox_policy_exact_url_params, test_project_bbox_to_service_crs_policy_exact_url_params, test_unsupported_crs_transform_fails_clearly, test_url_planning_is_deterministic, test_golden_manifest_bytes_are_stable |
| `tests/test_user_toggles.py` | 29 | `OK` | - | test_user_toggles_load_and_validate, test_user_toggles_reject_secret_like_value, test_write_effective_reports |
