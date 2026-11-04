// A stand-in for the SDK symbols the shim wraps. Records what got through. No SDK code or headers.
int fake_dof_calls, fake_last_dof = -1, fake_init_calls, fake_start_calls, fake_mode_calls, fake_mode_value, fake_dof_before_init;
int xr_device_provider_set_dof_type_carina(void* h, int is_6dof) { (void)h; ++fake_dof_calls; fake_last_dof = is_6dof; fake_dof_before_init = !fake_init_calls; return 0; }
int xr_device_provider_initialize(void* h, const char* a, const char* b) { (void)h; (void)a; (void)b; ++fake_init_calls; return 0; }
int xr_device_provider_start(void* h) { (void)h; ++fake_start_calls; return 0; }
int xr_device_provider_get_display_mode(void* h) { (void)h; ++fake_mode_calls; return fake_mode_value; }
