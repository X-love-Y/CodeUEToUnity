#include "PlayerCharacter.h"

#include "Camera/CameraComponent.h"
#include "Components/CapsuleComponent.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/SpringArmComponent.h"

APlayerCharacter::APlayerCharacter()
{
    PrimaryActorTick.bCanEverTick = true;

    GetCapsuleComponent()->InitCapsuleSize(42.0f, 96.0f);

    bUseControllerRotationPitch = false;
    bUseControllerRotationYaw = true;
    bUseControllerRotationRoll = false;

    // 角色朝移动方向转身
    GetCharacterMovement()->bOrientRotationToMovement = true;
    GetCharacterMovement()->RotationRate = FRotator(0.0f, 640.0f, 0.0f);
    GetCharacterMovement()->JumpZVelocity = 600.0f;
    GetCharacterMovement()->AirControl = 0.35f;
    GetCharacterMovement()->GravityScale = 1.75f;

    // 相机弹簧臂
    CameraBoom = CreateDefaultSubobject<USpringArmComponent>(TEXT("CameraBoom"));
    CameraBoom->SetupAttachment(RootComponent);
    CameraBoom->TargetArmLength = 300.0f;
    CameraBoom->SocketOffset = FVector(0.0f, 40.0f, 70.0f);
    CameraBoom->bUsePawnControlRotation = true;
    CameraBoom->bDoCollisionTest = true;

    FollowCamera = CreateDefaultSubobject<UCameraComponent>(TEXT("FollowCamera"));
    FollowCamera->SetupAttachment(CameraBoom, USpringArmComponent::SocketName);
    FollowCamera->bUsePawnControlRotation = false;

    ClimbComponent = CreateDefaultSubobject<UWallClimbComponent>(TEXT("ClimbComponent"));
}

void APlayerCharacter::BeginPlay()
{
    Super::BeginPlay();

    GetCharacterMovement()->MaxWalkSpeed = WalkSpeed;
    MovementState = EMovementState::Walking;

    UE_LOG(LogTemp, Log, TEXT("PlayerCharacter ready. WalkSpeed=%.1f SprintSpeed=%.1f"), WalkSpeed, SprintSpeed);
}

void APlayerCharacter::SetupPlayerInputComponent(UInputComponent* PlayerInputComponent)
{
    Super::SetupPlayerInputComponent(PlayerInputComponent);

    PlayerInputComponent->BindAxis("MoveForward", this, &APlayerCharacter::MoveForward);
    PlayerInputComponent->BindAxis("MoveRight", this, &APlayerCharacter::MoveRight);

    PlayerInputComponent->BindAxis("TurnRate", this, &APlayerCharacter::TurnAtRate);
    PlayerInputComponent->BindAxis("LookUpRate", this, &APlayerCharacter::LookUpAtRate);

    PlayerInputComponent->BindAction("Jump", IE_Pressed, this, &APlayerCharacter::OnJumpPressed);
    PlayerInputComponent->BindAction("Jump", IE_Released, this, &APlayerCharacter::OnJumpReleased);
    PlayerInputComponent->BindAction("Climb", IE_Pressed, this, &APlayerCharacter::OnClimbPressed);
    PlayerInputComponent->BindAction("Climb", IE_Released, this, &APlayerCharacter::OnClimbReleased);
}

void APlayerCharacter::Tick(float DeltaTime)
{
    Super::Tick(DeltaTime);

    UpdateMovementState();

    if (MovementState == EMovementState::Climbing)
    {
        ApplyClimbMovement(DeltaTime);
    }
    else
    {
        ApplyWalkMovement(DeltaTime);
    }

    UpdateCameraBoom(DeltaTime);
}

void APlayerCharacter::MoveForward(float Value)
{
    if (MovementState == EMovementState::Climbing)
    {
        // 爬墙时前后输入改为沿墙面上下移动
        return;
    }

    if (FMath::Abs(Value) > KINDA_SMALL_NUMBER && Controller)
    {
        const FRotator YawRotation(0.0f, Controller->GetControlRotation().Yaw, 0.0f);
        const FVector Direction = FRotationMatrix(YawRotation).GetUnitAxis(EAxis::X);

        AddMovementInput(Direction, Value);

        // 冲刺: 按住 Shift 由输入轴负值传入
        const float TargetSpeed = (Value > 1.5f) ? SprintSpeed : WalkSpeed;
        GetCharacterMovement()->MaxWalkSpeed = TargetSpeed;
    }
}

void APlayerCharacter::MoveRight(float Value)
{
    if (MovementState == EMovementState::Climbing)
    {
        // 爬墙时左右输入改为沿墙面横向平移
        const FVector Lateral = FVector::CrossProduct(ClimbSurfaceNormal, FVector::UpVector).GetSafeNormal();
        AddActorWorldOffset(Lateral * Value * ClimbSpeed * GetWorld()->GetDeltaSeconds(), true);
        return;
    }

    if (FMath::Abs(Value) > KINDA_SMALL_NUMBER && Controller)
    {
        const FRotator YawRotation(0.0f, Controller->GetControlRotation().Yaw, 0.0f);
        const FVector Direction = FRotationMatrix(YawRotation).GetUnitAxis(EAxis::Y);
        AddMovementInput(Direction, Value);
    }
}

void APlayerCharacter::TurnAtRate(float Rate)
{
    if (Rate != 0.0f)
    {
        AddControllerYawInput(Rate * MouseSensitivity);
    }
}

void APlayerCharacter::LookUpAtRate(float Rate)
{
    if (Rate != 0.0f)
    {
        AddControllerPitchInput(Rate * MouseSensitivity);
    }
}

void APlayerCharacter::OnJumpPressed()
{
    if (MovementState == EMovementState::Climbing)
    {
        // 爬墙中按跳跃 = 蹬墙跳开
        ClimbComponent->StopClimb();
        MovementState = EMovementState::Jumping;
        GetCharacterMovement()->SetMovementMode(MOVE_Falling);
        LaunchCharacter(ClimbSurfaceNormal * 500.0f + FVector::UpVector * 400.0f, false, true);
        return;
    }

    Jump();
    MovementState = EMovementState::Jumping;
}

void APlayerCharacter::OnJumpReleased()
{
    StopJumping();
}

void APlayerCharacter::OnClimbPressed()
{
    bClimbInputHeld = true;

    if (MovementState == EMovementState::Jumping || MovementState == EMovementState::Walking)
    {
        if (ClimbComponent->TryStartClimb())
        {
            MovementState = EMovementState::Climbing;
            ClimbSurfaceNormal = ClimbComponent->GetSurfaceNormal();

            // 爬墙时关闭重力并冻结角色旋转
            GetCharacterMovement()->SetMovementMode(MOVE_Flying);
            GetCharacterMovement()->bOrientRotationToMovement = false;
        }
    }
}

void APlayerCharacter::OnClimbReleased()
{
    bClimbInputHeld = false;

    if (MovementState == EMovementState::Climbing)
    {
        ClimbComponent->StopClimb();
        MovementState = EMovementState::Jumping;
        GetCharacterMovement()->SetMovementMode(MOVE_Falling);
        GetCharacterMovement()->bOrientRotationToMovement = true;
    }
}

void APlayerCharacter::UpdateMovementState()
{
    if (MovementState == EMovementState::Climbing)
    {
        // 爬墙过程中墙面消失(走出边缘)则自动脱离
        if (!ClimbComponent->IsOnClimbableSurface())
        {
            OnClimbReleased();
        }
        return;
    }

    if (MovementState == EMovementState::Walking || MovementState == EMovementState::Sprinting)
    {
        const float Speed = GetVelocity().Size2D();
        if (Speed > 1.0f)
        {
            MovementState = (Speed > WalkSpeed + 1.0f) ? EMovementState::Sprinting : EMovementState::Walking;
        }
        else
        {
            MovementState = EMovementState::Walking;
        }
    }
}

void APlayerCharacter::ApplyWalkMovement(float DeltaTime)
{
    if (MovementState == EMovementState::Jumping && GetCharacterMovement()->IsMovingOnGround())
    {
        MovementState = EMovementState::Walking;
    }
}

void APlayerCharacter::ApplyClimbMovement(float DeltaTime)
{
    if (!bClimbInputHeld)
    {
        return;
    }

    // 沿墙面竖直向上攀爬, 同时保持贴墙
    const FVector UpAlongWall = FVector::UpVector * ClimbSpeed;
    const FVector IntoWall = -ClimbSurfaceNormal * 30.0f;

    AddActorWorldOffset((UpAlongWall + IntoWall) * DeltaTime, true);
}

void APlayerCharacter::UpdateCameraBoom(float DeltaTime)
{
    if (!CameraBoom)
    {
        return;
    }

    // 限制俯仰角, 避免相机翻转
    const FRotator ControlRotation = GetControlRotation();
    const float ClampedPitch = FMath::Clamp(ControlRotation.Pitch, MinPitch, MaxPitch);

    if (!FMath::IsNearlyEqual(ControlRotation.Pitch, ClampedPitch, 0.01f))
    {
        FRotator Fixed(ClampedPitch, ControlRotation.Yaw, ControlRotation.Roll);
        GetController()->SetControlRotation(Fixed);
    }
}
