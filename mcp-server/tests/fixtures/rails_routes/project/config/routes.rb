File.write("ATLAS_S24_SENTINEL_TOP", "executed")

Rails.application.routes.draw do
  # supported literal routes
  get "/users", to: "users#index"
  get "/users/:id", to: "users#show", as: "user"
  get "/admin/users", to: "admin/users#index"
  delete "/admin/users/:id", to: "admin/users#destroy"
  post "/api/v1/widgets", to: "api/v1/widgets#create"
  patch '/api/v1/widgets/:id', to: 'api/v1/widgets#update'
  put "/reports/:id", to: "reports#show"
  get "/orders", to: "legacy/orders#index"
  get "/missing", to: "ghosts#index"
  get "/users/export", to: "users#export"
  get "/users/build", to: "users#build"
  get "/ping", to: "api/v1/ping#show"

  # unsupported declarations
  get "/greet/#{name}", to: "users#index"
  get "/dyn", to: "users##{action_name}"
  resources :photos
  mount Sidekiq::Web => "/sidekiq"
  get "/old", to: redirect("/users")
  get "/rocket" => "users#index"
  get "/ctrl", controller: "users", action: "index"
  get "/fmt", to: "users#index", format: false
  get "/bad", to: "Users#Index"
  get :sym_path, to: "users#index"
  get "/implicit"
  get "/blk", to: "users#index" do end
  get "/a", "/b", to: "users#index"
  namespace :admin do
    get "/stats", to: "stats#index"
  end
  scope "/v2" do
    get "/x", to: "users#index"
  end
  constraints(subdomain: "api") do
    get "/c", to: "users#index"
  end
  match "/legacy", to: "users#index", via: [:get, :post]
  root to: "users#index"
  get "/cond", to: "users#index" if ENV["FEATURE"]
  system("touch ATLAS_S24_SENTINEL_DRAW")
  draw :extra
  concern :commentable do end
end

Blog::Engine.routes.draw do
  get "/e", to: "users#index"
end
