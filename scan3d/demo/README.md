Hosted demo page (no camera; built-in sample + photo/video upload, zip export).
To assemble: copy ../web/{worker,viewer,export}.js and ../web/pkg here, and render the sample with
`cargo run --release --example synthetic -- --ppm sample 40` then convert the .ppm files to sample/frame_NN.jpg.
